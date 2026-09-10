"""Optional local Selenium remediation gate; requires system Chromium/ChromeDriver.

Starts disposable loopback Uvicorn/Vite, seeds authenticated test evidence, and
checks real layout and complete evidence. No project data leaves the machine.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from drishti_sdk import DrishtiClient


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    baseline = '--baseline' in sys.argv
    results = []
    with tempfile.TemporaryDirectory(prefix='astra-browser-') as directory:
        root = Path(directory)
        api, ui = free_port(), free_port()
        token = secrets.token_urlsafe(32)
        api_url, ui_url = f'http://127.0.0.1:{api}', f'http://127.0.0.1:{ui}'
        env = dict(os.environ, DRISHTI_DATA_DIR=str(root / 'data'), DRISHTI_KEY_DIR=str(root / 'keys'),
                   DRISHTI_ADMIN_BEARER_TOKEN=token, DRISHTI_INTAKE_ORIGIN=ui_url,
                   VITE_DRISHTI_API_URL=api_url, VITE_DRISHTI_DEMO_MODE='false')
        processes = []
        driver = None
        with (root / 'servers.log').open('w') as log:
            try:
                processes.append(subprocess.Popen([str(ROOT / '.venv/bin/python'), '-m', 'uvicorn',
                    'backend.main:app', '--host', '127.0.0.1', '--port', str(api)], cwd=ROOT, env=env, stdout=log, stderr=log))
                processes.append(subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1',
                    '--port', str(ui), '--strictPort'], cwd=ROOT / 'frontend', env=env, stdout=log, stderr=log))
                for url in [api_url + '/health', ui_url]:
                    for _ in range(100):
                        try:
                            if httpx.get(url, timeout=1).status_code == 200: break
                        except httpx.HTTPError: pass
                        time.sleep(.1)
                    else: raise RuntimeError('Local server did not start')
                with DrishtiClient(api_url, admin_token=token) as client:
                    key = Ed25519PrivateKey.generate()
                    client.register_producer('astra', 'ASTRA test producer', 'dataset_integrity')
                    client.register_producer_key('astra', 'astra-key', key.public_key().public_bytes(
                        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
                    for identity, count, sealed in [('B', 3, True), ('A', 12, False)]:
                        client.create_assessment(identity, identity)
                        client.activate_assessment(identity)
                        findings = [client.build_finding(finding_id=f'{identity}-{i:03d}', module='dataset_integrity',
                            asset_type='sample', asset_id=f'sample:{identity}-{i}', category='NEAR_DUPLICATE',
                            severity='HIGH', confidence=.9, reason='Local regression evidence', evidence=['distance=1'],
                            recommendation='REVIEW', limitations=['Synthetic regression fixture']) for i in range(count)]
                        run = client.build_run(module='dataset_integrity', assessment_id=identity,
                            producer='astra', producer_version='1', findings=findings)
                        client.submit_signed_run(run=run, key_id='astra-key', private_key=key)
                        if sealed: client.seal_assessment(identity)
                    client.create_assessment('EMPTY', 'Empty draft')
                options = webdriver.ChromeOptions()
                options.binary_location = '/usr/bin/chromium'
                for arg in ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage']: options.add_argument(arg)
                options.set_capability('goog:loggingPrefs', {'browser': 'ALL', 'performance': 'ALL'})
                driver = webdriver.Chrome(service=Service('/usr/bin/chromedriver'), options=options)
                wait = WebDriverWait(driver, 15)
                paths = ['/', '/findings', '/distribution', '/reports', '/new-assessment', '/system', '/graph']
                for width, height in [(390, 844), (1366, 768), (1920, 1080)]:
                    driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', dict(width=width, height=height,
                        deviceScaleFactor=1, mobile=False))
                    for path in paths:
                        driver.get(ui_url + path)
                        wait.until(lambda d: d.find_elements(By.ID, 'assessment-select') and
                                   Select(d.find_element(By.ID, 'assessment-select')).first_selected_option.get_attribute('value') == 'A')
                        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, 'main h1, .overview-workspace'))
                        if path == '/findings': wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.explorer-row')) == 12)
                        if path == '/reports': wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.report-finding')) == 12)
                        time.sleep(.15)
                        dimensions = driver.execute_script('return [document.documentElement.clientWidth,document.documentElement.scrollWidth]')
                        overflow = driver.execute_script('''return [...document.querySelectorAll('main *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,8).map(e=>({tag:e.tagName,cls:e.className,right:e.getBoundingClientRect().right,grid:getComputedStyle(e).gridTemplateColumns}))''')
                        result = dict(viewport=[width,height], path=path, dimensions=dimensions, overflow=overflow)
                        results.append(result)
                        print(json.dumps(result), flush=True)
                # Selection changes must replace the scoped evidence, including an empty result.
                driver.get(ui_url + '/findings')
                wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.explorer-row')) == 12)
                Select(driver.find_element(By.ID, 'assessment-select')).select_by_value('B')
                wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.explorer-row')) == 3)
                assert all('B-' in e.text for e in driver.find_elements(By.CSS_SELECTOR, '.explorer-row'))
                Select(driver.find_element(By.ID, 'assessment-select')).select_by_value('EMPTY')
                wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.explorer-row')) == 0)
                errors = [e for e in driver.get_log('browser') if e['level'] == 'SEVERE']
                network = [json.loads(e['message'])['message'] for e in driver.get_log('performance')]
                failed = [m['params'] for m in network if m['method'] == 'Network.loadingFailed' and not m['params'].get('canceled')]
                print(json.dumps(dict(console_errors=errors, network_failures=failed, selection='PASS')), flush=True)
                if not baseline:
                    assert all(r['dimensions'][1] <= r['dimensions'][0] for r in results), 'Horizontal overflow'
                    assert not failed
                    assert not [e for e in errors if 'favicon.ico' not in e['message']]
                print('BROWSER_MATRIX_' + ('BASELINE' if baseline else 'PASS'), flush=True)
            finally:
                if driver: driver.quit()
                for process in processes: process.terminate()
                for process in processes:
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()


if __name__ == '__main__':
    main()
