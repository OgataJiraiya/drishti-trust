"""Loopback-only capability endpoints. No generic administrative proxy."""
import ipaddress
import json
import os
from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import ValidationError

from backend.api.auth import require_admin
from backend.schemas.intake import IntakeCreate
from backend.services.intake_service import MAX_FILE, MAX_FILES, MAX_TOTAL

router = APIRouter(prefix='/api/intake', tags=['local assessment intake'])


def local(request: Request):
    try:
        loopback = ipaddress.ip_address(request.client.host).is_loopback
        host = request.url.hostname
        host_ok = host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except (ValueError, AttributeError):
        loopback = host_ok = False
    if not loopback or not host_ok:
        raise HTTPException(403, 'Intake requires a loopback connection and host')
    if not request.app.state.settings.intake_origin:
        raise HTTPException(503, 'Local intake is not enabled')


def capability(request: Request):
    local(request)
    if request.headers.get('origin') != request.app.state.settings.intake_origin:
        raise HTTPException(403, 'Intake origin rejected')
    return request.app.state.intake_service.authorize(request.headers.get('x-drishti-intake', ''))


@router.post('/capabilities', dependencies=[Depends(require_admin)])
def issue(request: Request):
    local(request)
    if request.headers.get('origin'):
        raise HTTPException(403, 'Mint capabilities from the local CLI only')
    return request.app.state.intake_service.issue()


@router.post('/job', status_code=201)
async def create(request: Request, auth=Depends(capability)):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 16384: raise HTTPException(413, 'Intake metadata too large')
    try: body = IntakeCreate.model_validate_json(data)
    except (ValidationError, ValueError): raise HTTPException(422, 'Invalid assessment metadata or behavioral contract')
    return request.app.state.intake_service.create(auth[0], body).public()


def owned(auth):
    if auth[1] is None: raise HTTPException(404, 'No intake job')
    return auth[1]


@router.get('/job')
def status(auth=Depends(capability)):
    return owned(auth).public()


@router.put('/job/files/{role}')
async def upload(role: str, request: Request, auth=Depends(capability)):
    job = owned(auth)
    service = request.app.state.intake_service
    filename = request.headers.get('x-drishti-filename', '')
    service.validate_filename(role, filename)
    if not job.lock.acquire(blocking=False): raise HTTPException(409, 'Intake job is busy')
    path = None
    try:
        if job.state != 'STAGING': raise HTTPException(409, 'Evidence staging is closed')
        if len(job.files) >= MAX_FILES: raise HTTPException(413, 'File count limit exceeded')
        if role in {'candidate', 'reference', 'corpus', 'inference', 'representation', 'prediction'} and any(x['role'] == role for x in job.files):
            raise HTTPException(409, 'Only one file is allowed for this role')
        total = sum(x['size'] for x in job.files)
        ceiling = min(MAX_FILE, MAX_TOTAL - total, 1024 * 1024 if role in {'inference', 'representation', 'prediction'} else MAX_FILE)
        try: declared = int(request.headers.get('content-length', '0'))
        except ValueError: raise HTTPException(422, 'Invalid content length')
        if declared > ceiling: raise HTTPException(413, 'Artifact exceeds intake byte limit')
        directory = job.root / role
        if directory.is_symlink() or job.root.is_symlink(): raise HTTPException(422, 'Symlink rejected')
        directory.mkdir(mode=0o700, exist_ok=True)
        logical_id = f'artifact-{len(job.files):03d}' + Path(filename).suffix.lower()
        path = directory / logical_id
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        size = 0
        digest = sha256()
        with os.fdopen(descriptor, 'wb') as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > ceiling: raise HTTPException(413, 'Artifact exceeds intake byte limit')
                target.write(chunk)
                digest.update(chunk)
        if size == 0: raise HTTPException(422, 'Empty evidence is not supported')
        item = {'role': role, 'filename': filename, 'logical_id': logical_id,
                'size': size, 'sha256': digest.hexdigest(), 'declared_format': Path(filename).suffix[1:].lower()}
        if role in {'candidate', 'reference'}:
            from sqlalchemy import select
            from backend.database.models import RegisteredModelRecord
            with request.app.state.session_factory() as session:
                records = session.scalars(select(RegisteredModelRecord).where(RegisteredModelRecord.expected_sha256 == item['sha256']).limit(20))
                item['registry_matches'] = [{'model_id': record.model_id, 'status': record.status} for record in records]
        job.files.append(item)
        path = None
        return item
    finally:
        if path is not None: path.unlink(missing_ok=True)
        job.lock.release()


@router.post('/job/run', status_code=202)
def run(request: Request, background: BackgroundTasks, auth=Depends(capability)):
    job = owned(auth)
    if not job.lock.acquire(blocking=False): raise HTTPException(409, 'Intake job is busy')
    try:
        if job.state != 'STAGING': raise HTTPException(409, 'Assessment already started')
        roles = {x['role'] for x in job.files}
        if 'reference' in roles and 'candidate' not in roles: raise HTTPException(422, 'Reference requires a candidate')
        if job.metadata.behavioral and not {'candidate', 'corpus'} <= roles: raise HTTPException(422, 'Behavioral execution requires candidate and corpus')
        if 'corpus' in roles and not job.metadata.behavioral: raise HTTPException(422, 'Behavioral execution requires explicit opt-in')
        if not ('candidate' in roles or 'dataset' in roles or 'inference' in roles or 'representation' in roles or 'prediction' in roles or {'distribution_reference', 'distribution_current'} <= roles):
            raise HTTPException(422, 'Supply sufficient evidence for at least one module')
        job.state = 'RUNNING'
        job.phase = 'Queued for local detector execution'
        background.add_task(request.app.state.intake_service.execute, job)
        return job.public()
    finally: job.lock.release()


@router.delete('/job')
def cancel(request: Request, auth=Depends(capability)):
    job = owned(auth)
    if not job.lock.acquire(blocking=False): raise HTTPException(409, 'Intake job is busy')
    try:
        if job.state == 'RUNNING': raise HTTPException(409, 'Execution is in progress; bounded workers must finish before cleanup')
        request.app.state.intake_service.cleanup(job)
        job.state = 'CANCELLED'
        return job.public()
    finally: job.lock.release()
