"""Behavioral regressions for receipt allocation and scoped evidence retrieval."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from backend.database.models import InferenceReceiptRecord, ModuleRunAuthenticationRecord
from backend.database.repository import ReceiptRepository
from backend.schemas.inference import CreateReceiptRequest
from backend.tests.test_assessment_lifecycle import create
from backend.tests.test_evidence import dataset_finding
from backend.tests.test_module_auth import register_identity, signed_body


@pytest.mark.parametrize('workers', [2, 5])
def test_receipt_concurrency_preserves_chain(app, creation_payload, monkeypatch, workers):
    start = Barrier(workers)
    allocated = Barrier(workers, timeout=1)
    original = ReceiptRepository.next_sequence

    def synchronized_read(repository):
        value = original(repository)
        # Before serialization all readers meet here with the same candidate.
        # With serialization only the writer arrives; the bounded rendezvous
        # expires, letting it commit before the other readers can proceed.
        try:
            allocated.wait()
        except BrokenBarrierError:
            pass
        return value

    monkeypatch.setattr(ReceiptRepository, 'next_sequence', synchronized_read)
    def create_one(_):
        with app.state.session_factory() as session:
            start.wait(timeout=5)
            return app.state.provenance_service.create_receipt(
                CreateReceiptRequest(**creation_payload), session)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(create_one, i) for i in range(workers)]
        receipts = [future.result(timeout=15) for future in futures]
    assert sorted(r.security.sequence for r in receipts) == list(range(1, workers + 1))
    with app.state.session_factory() as session:
        records = list(session.scalars(select(InferenceReceiptRecord).order_by(InferenceReceiptRecord.sequence)))
    by_sequence = {r.security.sequence: r for r in receipts}
    assert by_sequence[1].security.previous_receipt_hash is None
    for previous, current in zip(records, records[1:]):
        assert by_sequence[current.sequence].security.previous_receipt_hash == previous.receipt_hash


def test_receipt_rollback_and_repeated_session(app, creation_payload, monkeypatch):
    request = CreateReceiptRequest(**creation_payload)
    with app.state.session_factory() as session:
        with monkeypatch.context() as patch:
            def fail_commit():
                session.flush()
                raise RuntimeError('controlled commit failure')
            patch.setattr(session, 'commit', fail_commit)
            with pytest.raises(RuntimeError, match='controlled commit failure'):
                app.state.provenance_service.create_receipt(request, session)
        session.rollback()
        assert not session.in_transaction()
        first = app.state.provenance_service.create_receipt(request, session)
        second = app.state.provenance_service.create_receipt(request, session)
        assert not session.in_transaction()
    with app.state.session_factory() as session:
        third = app.state.provenance_service.create_receipt(request, session)
    assert [r.security.sequence for r in (first, second, third)] == [1, 2, 3]


@pytest.mark.anyio
async def test_scoped_findings_complete_paginated_and_isolated(client):
    key = Ed25519PrivateKey.generate()
    await register_identity(client, key)
    for identity, count in [('A', 12), ('B', 3)]:
        assert (await create(client, identity)).status_code == 201
        assert (await client.post(f'/api/assessments/{identity}/activate')).status_code == 200
        findings = [{**dataset_finding(), 'finding_id': f'{identity}-{i:03d}'} for i in range(count)]
        run = dict(run_id=f'RUN-{identity}', assessment_id=identity, module='dataset_integrity',
                   producer='data-integrity', producer_version='1', findings=findings)
        assert (await client.post('/api/integration/signed-runs', json=signed_body(run, 'KEY-DATA-001', key))).status_code == 200
        assert (await client.post(f'/api/assessments/{identity}/seal')).status_code == 200
    summary = (await client.get('/api/summary?assessment_id=A')).json()
    assert len(summary['latest_findings']) == 10
    pages = []
    for page in range(1, 4):
        response = await client.get(f'/api/assessments/A/findings?page={page}&page_size=5')
        assert response.status_code == 200
        assert response.json()['total'] == 12
        pages.extend(response.json()['items'])
    assert [item['finding_id'] for item in pages] == [f'A-{i:03d}' for i in range(12)]
    assert (await client.get('/api/assessments/A/findings?page=4&page_size=5')).json()['items'] == []
    assert (await client.get('/api/assessments/B/findings')).json()['total'] == 3
    assert (await client.get('/api/assessments/MISSING/findings')).status_code == 404
    for query in ['page=0', 'page=-1', 'page=99999999999999999999', 'page_size=0',
                  'page_size=101', 'page_size=999999999', 'offset=-1', 'limit=999999999']:
        assert (await client.get('/api/assessments/A/findings?' + query)).status_code == 422
    assert (await client.get('/api/assessments/' + 'X' * 129 + '/findings')).status_code == 422
    assert (await client.get('/api/assessments/A/snapshot/verify')).json()['status'] == 'VALID'
    await create(client, 'EMPTY')
    assert (await client.get('/api/assessments/EMPTY/findings')).json()['items'] == []


@pytest.mark.anyio
async def test_scoped_findings_require_matching_authentication_and_deduplicate(client, app):
    key = Ed25519PrivateKey.generate()
    await register_identity(client, key)
    await create(client, 'AUTH')
    await client.post('/api/assessments/AUTH/activate')
    shared = {**dataset_finding(), 'finding_id': 'SHARED'}
    for run_id in ['FIRST', 'SECOND']:
        run = dict(run_id=run_id, assessment_id='AUTH', module='dataset_integrity',
                   producer='data-integrity', producer_version='1', findings=[shared])
        assert (await client.post('/api/integration/signed-runs', json=signed_body(run, 'KEY-DATA-001', key))).status_code == 200
    # Direct persisted evidence has no authenticated membership and must not leak.
    orphan = {**dataset_finding(), 'finding_id': 'ORPHAN'}
    assert (await client.post('/api/evidence', json=orphan)).status_code in (200, 201)
    url = '/api/assessments/AUTH/findings'
    assert (await client.get(url)).json()['total'] == 1
    # Simulate damaged persistence: a mode or commitment mismatch fails closed.
    with app.state.session_factory() as session:
        records = list(session.scalars(select(ModuleRunAuthenticationRecord)))
        for record in records:
            record.authentication_mode = 'TRUSTED_INTERNAL'
        session.commit()
    assert (await client.get(url)).json()['items'] == []
    with app.state.session_factory() as session:
        for record in session.scalars(select(ModuleRunAuthenticationRecord)):
            record.authentication_mode = 'ED25519'
            record.request_hash = '0' * 64
        session.commit()
    assert (await client.get(url)).json()['items'] == []


def test_ci_actions_are_immutable():
    import re
    from pathlib import Path
    workflow = Path('.github/workflows/ci.yml').read_text()
    actions = re.findall(r'uses:\s+(\S+)', workflow)
    assert actions
    assert all(re.fullmatch(r'actions/[\w-]+@[0-9a-f]{40}', action) for action in actions)
    assert 'permissions:\n  contents: read' in workflow
