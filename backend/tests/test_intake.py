"""Real local ONNX intake and fail-closed authorization regression tests."""
import base64
import io
import time
from hashlib import sha256

import httpx
import numpy as np
import onnx
from onnx import helper, TensorProto
from PIL import Image
import pytest

from backend.core.config import Settings
from backend.main import create_app
from backend.services.intake_service import MAX_FILE

ORIGIN = 'http://127.0.0.1:5173'


def model_bytes():
    tensor = lambda name: helper.make_tensor_value_info(name, TensorProto.FLOAT, [1, 3, 8, 8])
    model = helper.make_model(helper.make_graph([helper.make_node('Identity', ['input'], ['output'])],
        'organization-test', [tensor('input')], [tensor('output')]), opset_imports=[helper.make_opsetid('', 18)])
    model.ir_version = 10
    return model.SerializeToString()


@pytest.fixture
async def intake(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / 'data', key_dir=tmp_path / 'keys',
        admin_bearer_token='local-admin', intake_origin=ORIGIN))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)), base_url='http://127.0.0.1:8000') as client:
        response = await client.post('/api/intake/capabilities', headers={'Authorization': 'Bearer local-admin'})
        assert response.status_code == 200
        headers = {'Origin': ORIGIN, 'X-Drishti-Intake': response.json()['capability']}
        yield app, client, headers


async def stage(client, headers, role, name, data):
    return await client.put('/api/intake/job/files/' + role, content=data, headers={**headers, 'X-Drishti-Filename': name})


@pytest.mark.anyio
async def test_authorization_expiry_origin_and_scope(intake):
    app, client, headers = intake
    assert (await client.post('/api/intake/job', json={'name': 'x'})).status_code == 403
    assert (await client.post('/api/intake/job', json={'name': 'x'}, headers={'Origin': ORIGIN})).status_code == 401
    assert (await client.post('/api/intake/job', json={'name': 'x'}, headers={**headers, 'Origin': 'https://example.org'})).status_code == 403
    assert (await client.post('/api/assessments', json={'assessment_id': 'x', 'name': 'x'}, headers=headers)).status_code == 401
    digest = sha256(headers['X-Drishti-Intake'].encode()).hexdigest()
    app.state.intake_service.capabilities[digest] = (time.monotonic() - 1, None)
    assert (await client.post('/api/intake/job', json={'name': 'x'}, headers=headers)).status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize('filename,status', [('../model.onnx', 422), ('evil.pkl', 415), ('a\\b.onnx', 422), ('model.zip', 415)])
async def test_unsafe_upload_rejected(intake, filename, status):
    _, client, headers = intake
    await client.post('/api/intake/job', json={'name': 'x'}, headers=headers)
    assert (await stage(client, headers, 'candidate', filename, b'bad')).status_code == status


@pytest.mark.anyio
async def test_size_symlink_and_cleanup(intake, tmp_path):
    app, client, headers = intake
    result = await client.post('/api/intake/job', json={'name': 'x'}, headers=headers)
    root = app.state.intake_service.root / result.json()['assessment_id']
    assert root.stat().st_mode & 0o777 == 0o700
    response = await client.put('/api/intake/job/files/candidate', content=b'bad', headers={**headers, 'X-Drishti-Filename': 'm.onnx', 'Content-Length': str(MAX_FILE + 1)})
    assert response.status_code == 413
    (root / 'candidate').symlink_to(tmp_path, target_is_directory=True)
    assert (await stage(client, headers, 'candidate', 'm.onnx', b'bad')).status_code == 422
    assert (await client.delete('/api/intake/job', headers=headers)).status_code == 200
    assert not root.exists()


@pytest.mark.anyio
@pytest.mark.parametrize('scenario', ['candidate', 'reference', 'behavioral', 'full'])
async def test_real_organization_scenarios(intake, scenario):
    app, client, headers = intake
    body = {'name': 'Organization model assessment'}
    if scenario == 'behavioral':
        body['behavioral'] = {'execute': True, 'input_name': 'input', 'output_name': 'output',
            'layout': 'NCHW', 'value_min': 0, 'value_max': 1, 'class_axis': -1}
    created = await client.post('/api/intake/job', json=body, headers=headers)
    assert created.status_code == 201, created.text
    identity = created.json()['assessment_id']
    payload = model_bytes()
    uploaded = await stage(client, headers, 'candidate', 'candidate.onnx', payload)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()['sha256'] == sha256(payload).hexdigest()
    if scenario == 'reference':
        assert (await stage(client, headers, 'reference', 'reference.onnx', payload)).status_code == 200
    if scenario == 'behavioral':
        stream = io.BytesIO()
        np.save(stream, np.ones((2, 1, 3, 8, 8), dtype=np.float32) * .5, allow_pickle=False)
        assert (await stage(client, headers, 'corpus', 'corpus.npy', stream.getvalue())).status_code == 200
    if scenario == 'full':
        for role in ['dataset', 'distribution_reference', 'distribution_current']:
            for index in range(20):
                stream = io.BytesIO()
                Image.new('RGB', (8, 8), 'white' if role == 'distribution_current' else 'black').save(stream, format='PNG')
                assert (await stage(client, headers, role, f'{index}.png', stream.getvalue())).status_code == 200
        import json
        reference = np.random.default_rng(42).normal(size=(40, 4))
        pair = {'descriptor': {'extractor_name': 'organization-extractor', 'extractor_version': '1', 'output_dimension': 4, 'normalization': 'NONE'}, 'reference': reference.tolist(), 'current': (reference + 5).tolist()}
        assert (await stage(client, headers, 'representation', 'representation.json', json.dumps(pair).encode())).status_code == 200
        pair = {'descriptor': {'evidence_tier': 'FULL_PROBABILITIES', 'class_labels': ['A', 'B'], 'output_family': 'CLASSIFICATION', 'probability_semantics': 'CLASS_PROBABILITIES', 'abstention_semantics': 'caller', 'unknown_semantics': 'caller'},
            'reference': [{'predicted_label': 'A', 'confidence': .9, 'probabilities': [.9, .1], 'abstained': False, 'unknown_or_ood': False} for _ in range(40)],
            'current': [{'predicted_label': 'B', 'confidence': .9, 'probabilities': [.1, .9], 'abstained': False, 'unknown_or_ood': False} for _ in range(40)]}
        assert (await stage(client, headers, 'prediction', 'prediction.json', json.dumps(pair).encode())).status_code == 200
        receipt = await client.post('/api/inference/receipt', json={'filename': 'sample.bin',
            'input_base64': base64.b64encode(b'organization-input').decode(), 'model_id': 'organization-model',
            'model_sha256': sha256(payload).hexdigest(), 'output': {'value': 1}})
        import json
        assert (await stage(client, headers, 'inference', 'receipt.json', json.dumps({'receipt': receipt.json()}).encode())).status_code == 200
    started = await client.post('/api/intake/job/run', headers=headers)
    assert started.status_code == 202, started.text
    job = (await client.get('/api/intake/job', headers=headers)).json()
    assert job['state'] == 'COMPLETE', job
    assert job['detail']['lifecycle'] == 'SEALED'
    assert not (app.state.intake_service.root / identity).exists()
    assert str(app.state.settings.data_dir) not in str(job)
    runs = (await client.get(f'/api/assessments/{identity}/runs')).json()['items']
    assert len(runs) == (4 if scenario == 'full' else 1)
    assert all(run['authentication']['authenticated'] and run['authentication']['mode'] == 'ED25519' for run in runs)
    assert (await client.get(f'/api/assessments/{identity}/snapshot/verify')).json()['status'] == 'VALID'
    if scenario == 'full':
        assert job['detail']['distribution']['interpretation'] == 'BROAD_MULTILAYER_SHIFT'
    if scenario != 'full':
        assert job['modules']['dataset_integrity'] == 'NOT PROVIDED'
        assert job['modules']['distribution_shift'] == 'NOT PROVIDED'
        # Frozen scoring intentionally treats a zero-Finding run as UNKNOWN.
        # A completed detector is not enough to manufacture score coverage.
        assert job['detail']['summary']['overall']['assessment_coverage'] < 1
    if scenario == 'reference': assert job['detail']['comparison'] in {'COMPLETE', 'PARTIAL'}
    if scenario == 'behavioral': assert job['detail']['behavioral'] == 'COMPLETE'


@pytest.mark.anyio
async def test_invalid_model_never_manufactures_completion(intake):
    app, client, headers = intake
    created = await client.post('/api/intake/job', json={'name': 'Invalid model'}, headers=headers)
    await stage(client, headers, 'candidate', 'invalid.onnx', b'not ONNX')
    await client.post('/api/intake/job/run', headers=headers)
    job = (await client.get('/api/intake/job', headers=headers)).json()
    assert job['state'] == 'FAILED'
    assert not (app.state.intake_service.root / created.json()['assessment_id']).exists()
    assert (await client.get('/api/assessments')).json()['total'] == 0


@pytest.mark.anyio
async def test_expired_staging_is_cleaned_and_capability_is_single_job(intake):
    app, client, headers = intake
    created = await client.post('/api/intake/job', json={'name': 'expires'}, headers=headers)
    assert (await client.post('/api/intake/job', json={'name': 'second'}, headers=headers)).status_code == 409
    digest = sha256(headers['X-Drishti-Intake'].encode()).hexdigest()
    _, job = app.state.intake_service.capabilities[digest]
    app.state.intake_service.capabilities[digest] = (time.monotonic() - 1, job)
    app.state.intake_service.reap()
    assert not (app.state.intake_service.root / created.json()['assessment_id']).exists()


@pytest.mark.anyio
async def test_wrong_host_and_narrow_cors(intake):
    _, client, headers = intake
    assert (await client.get('/api/intake/job', headers={**headers, 'Host': 'attacker.example'})).status_code == 403
    preflight = {'Origin': ORIGIN, 'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'x-drishti-intake,x-drishti-filename'}
    allowed = await client.options('/api/intake/job/files/candidate', headers=preflight)
    assert allowed.status_code == 200
    assert allowed.headers['access-control-allow-origin'] == ORIGIN
    assert '*' not in allowed.headers['access-control-allow-methods']
    assert (await client.options('/api/models/register/artifact', headers=preflight)).status_code == 400


@pytest.mark.anyio
async def test_missing_corpus_and_reference_only_do_not_execute(intake):
    _, client, headers = intake
    await client.post('/api/intake/job', json={'name': 'No candidate'}, headers=headers)
    await stage(client, headers, 'reference', 'reference.onnx', model_bytes())
    assert (await client.post('/api/intake/job/run', headers=headers)).status_code == 422
    assert (await client.get('/api/assessments')).json()['total'] == 0


@pytest.mark.anyio
async def test_missing_distribution_reference_does_not_submit_run(intake):
    _, client, headers = intake
    await client.post('/api/intake/job', json={'name': 'Partial input'}, headers=headers)
    await stage(client, headers, 'candidate', 'candidate.onnx', model_bytes())
    await stage(client, headers, 'distribution_current', 'current.png', b'not an image')
    await client.post('/api/intake/job/run', headers=headers)
    job = (await client.get('/api/intake/job', headers=headers)).json()
    assert job['state'] == 'COMPLETE'
    assert job['modules']['distribution_shift'] == 'NOT PROVIDED'
