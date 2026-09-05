"""Mint a local, ephemeral intake-only capability; never print the admin bearer."""
import os
from urllib.parse import urlparse
import httpx


def main():
    base = os.environ.get('DRISHTI_API_URL', 'http://127.0.0.1:8000')
    url = urlparse(base)
    if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'} or url.username or url.password:
        raise SystemExit('A loopback HTTP backend URL is required')
    token = os.environ.get('DRISHTI_ADMIN_BEARER_TOKEN')
    if not token: raise SystemExit('Set the local backend administrative credential in this terminal')
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        response = client.post(base.rstrip('/') + '/api/intake/capabilities', headers={'Authorization': 'Bearer ' + token})
    if response.status_code != 200:
        raise SystemExit(f'Capability creation failed ({response.status_code})')
    body = response.json()
    print(f"Origin: {body['origin']} · expires in {body['expires_in']} seconds")
    print(body['capability'])


if __name__ == '__main__': main()
