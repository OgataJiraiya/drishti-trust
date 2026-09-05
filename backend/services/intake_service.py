"""Bounded local workstation intake, with the existing detector and signing gates.

Capabilities and execution keys exist only in process memory. One capability owns
one job. Intake never accepts caller-supplied Findings or model execution code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import secrets
import shutil
import stat
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from backend.schemas.assessments import AssessmentCreate
from backend.schemas.intake import IntakeCreate
from backend.schemas.module_auth import SignedModuleRunSubmission
from backend.schemas.producers import ProducerRegistration, ProducerKeyRegistration
from backend.services.integration_service import RunAuthentication
from drishti_sdk import DrishtiClient

MODULES = ('dataset_integrity', 'model_integrity', 'inference_integrity', 'distribution_shift')
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 128
TTL = 1800
ROLES = {'candidate': {'.onnx'}, 'reference': {'.onnx'}, 'corpus': {'.npy'},
         'dataset': {'.png', '.jpg', '.jpeg', '.bmp', '.webp'},
         'distribution_reference': {'.png', '.jpg', '.jpeg', '.bmp', '.webp'},
         'distribution_current': {'.png', '.jpg', '.jpeg', '.bmp', '.webp'},
         'inference': {'.json'}, 'representation': {'.json'}, 'prediction': {'.json'}}


@dataclass
class IntakeJob:
    assessment_id: str
    metadata: IntakeCreate
    root: Path
    expires: float
    state: str = 'STAGING'
    phase: str = 'Awaiting evidence'
    files: list[dict] = field(default_factory=list)
    modules: dict = field(default_factory=lambda: dict.fromkeys(MODULES, 'NOT PROVIDED'))
    detail: dict = field(default_factory=dict)
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def public(self):
        return {'assessment_id': self.assessment_id, 'state': self.state, 'phase': self.phase,
                'files': self.files, 'modules': self.modules, 'detail': self.detail, 'error': self.error}


class IntakeService:
    def __init__(self, app):
        self.app = app
        self.capabilities: dict[str, tuple[float, IntakeJob | None]] = {}
        self.lock = threading.Lock()
        self.execution_lock = threading.Lock()
        self.root = app.state.settings.data_dir / 'intake'
        if self.root.is_symlink():
            raise RuntimeError('Intake root must not be a symlink')
        self.root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        # Single local backend process owns this directory. Recover orphaned staging.
        for path in self.root.iterdir():
            if re.fullmatch(r'ORG-[0-9a-f]{32}', path.name) and path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)

    def reap(self):
        with self.lock:
            for digest, (expires, job) in list(self.capabilities.items()):
                if expires <= time.monotonic() and (job is None or job.state != 'RUNNING'):
                    if job is not None and job.lock.acquire(blocking=False):
                        try: self.cleanup(job)
                        finally: job.lock.release()
                    elif job is not None:
                        continue
                    del self.capabilities[digest]

    def issue(self):
        self.reap()
        with self.lock:
            if len(self.capabilities) >= 8:
                raise HTTPException(429, 'Intake capacity reached')
            token = secrets.token_urlsafe(32)
            self.capabilities[sha256(token.encode()).hexdigest()] = (time.monotonic() + TTL, None)
        return {'capability': token, 'expires_in': TTL, 'origin': self.app.state.settings.intake_origin}

    def authorize(self, token):
        digest = sha256(token.encode()).hexdigest()
        with self.lock:
            record = self.capabilities.get(digest)
        if record is None or record[0] <= time.monotonic():
            raise HTTPException(401, 'Intake capability missing or expired')
        return digest, record[1]

    def create(self, digest, metadata):
        with self.lock:
            expires, existing = self.capabilities[digest]
            if existing is not None:
                raise HTTPException(409, 'Capability already owns an intake job')
            identity = 'ORG-' + secrets.token_hex(16)
            root = self.root / identity
            root.mkdir(mode=0o700)
            job = IntakeJob(identity, metadata, root, expires)
            self.capabilities[digest] = (expires, job)
        return job

    @staticmethod
    def cleanup(job):
        if job.root.exists():
            shutil.rmtree(job.root)

    @staticmethod
    def validate_filename(role, filename):
        if role not in ROLES or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_. -]{0,127}', filename) or '..' in filename:
            raise HTTPException(422, 'Unsupported role or unsafe filename')
        if Path(filename).suffix.lower() not in ROLES[role]:
            raise HTTPException(415, 'Unsupported evidence format')

    @staticmethod
    def paths(job, role):
        result = []
        for item in job.files:
            if item['role'] != role: continue
            path = job.root / role / item['logical_id']
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink() or path.parent.is_symlink() or job.root.is_symlink():
                raise ValueError('Evidence must be a regular file')
            # Recheck immutable staged content immediately before detector use.
            with path.open('rb') as source:
                digest = sha256(source.read(MAX_FILE + 1)).hexdigest()
            if info.st_size != item['size'] or digest != item['sha256']:
                raise ValueError('Staged evidence identity changed')
            result.append(path)
        return result

    def execute(self, job):
        state = self.app.state
        try:
            with self.execution_lock, DrishtiClient() as client:
                findings = {}
                candidate = self.paths(job, 'candidate')
                reference = self.paths(job, 'reference')
                corpus = self.paths(job, 'corpus')
                if candidate:
                    from modules.model_integrity.service import ModelIntegrityService
                    from modules.model_integrity.baseline import BaselineComparisonService
                    from modules.model_integrity.findings import ModelIntegrityEvidenceBundle
                    from modules.model_integrity.integration import ModelIntegrityRunBuilder
                    job.modules['model_integrity'] = 'RUNNING'
                    job.phase = 'Static model analysis'
                    manifest = ModelIntegrityService(max_onnx_parse_bytes=MAX_FILE).inspect(candidate[0], strict=True)
                    comparison = BaselineComparisonService().compare(reference[0], candidate[0]) if reference else None
                    behavioral = None
                    job.detail['static'] = 'M1 / M2'
                    job.detail['comparison'] = comparison.status.value if comparison else 'NOT ASSESSED'
                    if comparison:
                        job.detail['M4'] = {'artifact': comparison.artifact.state, 'structure': comparison.structure.state}
                    job.detail['behavioral'] = 'NOT ASSESSED'
                    if job.metadata.behavioral is not None:
                        from modules.model_integrity.cli import _load_bounded_npy
                        from modules.model_integrity.behavioral import BehavioralIntegrityService
                        from modules.model_integrity.behavioral_models import InputContract, OutputContract, InputLayout, ClassificationKind
                        import numpy as np
                        spec = job.metadata.behavioral
                        job.phase = 'Bounded behavioral execution'
                        data = _load_bounded_npy(corpus[0], MAX_FILE)
                        if not 0 < data.shape[0] <= 32:
                            raise ValueError('Behavior corpus requires 1–32 samples')
                        shape = list(data.shape[1:])
                        contract = InputContract(spec.input_name, data.dtype.name, shape, InputLayout(spec.layout),
                            shape[1 if spec.layout == 'NCHW' else 3], spec.value_min, spec.value_max)
                        output = OutputContract(spec.output_name, ClassificationKind(spec.classification_kind) if spec.classification_kind else None, spec.class_axis)
                        behavioral = BehavioralIntegrityService().analyze(candidate[0], [np.ascontiguousarray(x) for x in data], contract, output)
                        job.detail['behavioral'] = behavioral.status.value
                    findings['model_integrity'] = ModelIntegrityRunBuilder(client).map_findings(
                        ModelIntegrityEvidenceBundle(manifest=manifest, comparison=comparison, behavioral=behavioral))
                    job.modules['model_integrity'] = 'COMPLETE'
                dataset = self.paths(job, 'dataset')
                if dataset:
                    from modules.data_integrity.analyzer import analyze_dataset
                    from modules.data_integrity.adapter import DatasetIntegrityRunBuilder
                    job.phase = 'Dataset Integrity'
                    job.modules['dataset_integrity'] = 'RUNNING'
                    report = analyze_dataset(str(job.root / 'dataset'), max_images=MAX_FILES,
                        max_pair_comparisons=8192, max_findings=100, max_file_size=MAX_FILE,
                        run_labels=False, run_risk=False)
                    if report['detector_errors']:
                        raise ValueError('Dataset detector unavailable')
                    findings['dataset_integrity'] = DatasetIntegrityRunBuilder(client).map_findings(report['findings'])
                    job.detail['dataset'] = 'Image-only checks; labels and contributor risk NOT ASSESSED'
                    job.modules['dataset_integrity'] = 'COMPLETE'
                refdata = self.paths(job, 'distribution_reference')
                curdata = self.paths(job, 'distribution_current')
                representation = self.paths(job, 'representation')
                prediction = self.paths(job, 'prediction')
                if (refdata and curdata) or representation or prediction:
                    from modules.distribution_shift.profiling import ImageWindowProfiler
                    from modules.distribution_shift.comparison import DistributionShiftComparator
                    from modules.distribution_shift.interpretation import MultiSignalDriftInterpreter
                    from modules.distribution_shift.integration import DistributionShiftRunBuilder
                    job.phase = 'Distribution Shift'
                    job.modules['distribution_shift'] = 'RUNNING'
                    profiler = ImageWindowProfiler()
                    report = None
                    if refdata and curdata:
                        reference_profile = profiler.profile_reference(job.root / 'distribution_reference')
                        current_profile = profiler.profile_current(job.root / 'distribution_current')
                        if not reference_profile.sample_count_profiled or not current_profile.sample_count_profiled:
                            raise ValueError('Distribution windows contain no usable images')
                        report = DistributionShiftComparator().compare(reference_profile, current_profile)
                    from backend.services.intake_distribution import representation_pair, prediction_pair
                    d3 = representation_pair(representation[0]) if representation else None
                    d4 = prediction_pair(prediction[0]) if prediction else None
                    interpretation = MultiSignalDriftInterpreter().interpret(report, d3, d4)
                    findings['distribution_shift'] = DistributionShiftRunBuilder(client).map_findings(interpretation)
                    job.detail['distribution'] = {'image_statistical': report.status.value if report else 'NOT PROVIDED', 'representation': d3.status.value if d3 else 'NOT PROVIDED', 'prediction_output': d4.status.value if d4 else 'NOT PROVIDED', 'interpretation': interpretation.pattern_code.value}
                    job.modules['distribution_shift'] = 'COMPLETE'
                receipts = self.paths(job, 'inference')
                if receipts:
                    from backend.schemas.inference import VerifyReceiptRequest
                    from backend.services.finding_adapters import output_tampering_to_common_finding
                    job.phase = 'Inference Integrity receipt verification'
                    job.modules['inference_integrity'] = 'RUNNING'
                    evidence = VerifyReceiptRequest.model_validate_json(receipts[0].read_bytes())
                    verified = state.provenance_service.verify(evidence.receipt, evidence.artifacts)
                    if verified.checks.get('signature') != 'VALID':
                        raise ValueError('Receipt signature is not authenticated by the local trust root')
                    if any(item.attack_class != 'OUTPUT_TAMPERING' for item in verified.findings):
                        raise ValueError('Receipt evidence needs an unsupported Finding mapping')
                    findings['inference_integrity'] = [output_tampering_to_common_finding(evidence.receipt.receipt_id, item) for item in verified.findings]
                    job.detail['inference'] = 'Signed receipt verification only; VERIFY != ACCEPT. Replay acceptance NOT ASSESSED.'
                    job.modules['inference_integrity'] = 'COMPLETE'
                # Fail closed if any detector accidentally emits a workstation path.
                for batch in findings.values():
                    for finding in batch:
                        if str(job.root) in finding.model_dump_json():
                            raise ValueError('Detector evidence contains private staging paths')
                if not findings:
                    raise ValueError('No assessable evidence supplied')
                with state.session_factory() as session:
                    job.phase = 'Creating assessment'
                    state.assessment_service.create(AssessmentCreate(assessment_id=job.assessment_id,
                        name=job.metadata.name, description=job.metadata.notes or None,
                        metadata={'pipeline_label': job.metadata.version, 'intake': job.files, 'analysis': job.detail}), session)
                    job.detail['lifecycle'] = 'DRAFT'
                    state.assessment_service.activate(job.assessment_id, session)
                    job.detail['lifecycle'] = 'ACTIVE'
                    job.phase = 'Authenticating module evidence'
                    for module, batch in findings.items():
                        producer = f'{job.assessment_id}-{module}'
                        key_id = producer + '-key'
                        key = Ed25519PrivateKey.generate()
                        pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')
                        session.rollback()
                        state.producer_service.register_producer(ProducerRegistration(producer_id=producer, display_name='Local intake detector', module=module), session)
                        session.rollback()
                        state.producer_service.register_key(producer, ProducerKeyRegistration(key_id=key_id, public_key_pem=pem), session)
                        run = client.build_run(module=module, assessment_id=job.assessment_id, producer=producer, producer_version='1', findings=batch)
                        signed = SignedModuleRunSubmission(run=run, key_id=key_id, signature=client.sign_run(run, private_key=key))
                        authentication = state.module_auth_service.authenticate(signed, session)
                        session.rollback()
                        state.integration_service.ingest_run(run, session, RunAuthentication(mode='ED25519',
                            producer_id=authentication.producer_id, key_id=authentication.key_id, key_fingerprint=authentication.key_fingerprint))
                    job.phase = 'Computing backend assurance and sealing assessment'
                    session.rollback()
                    sealed = state.assessment_service.seal(job.assessment_id, session, state.audit_service)
                    job.detail['lifecycle'] = sealed.assessment.status
                    job.detail['findings'] = sealed.snapshot.trusted_finding_count
                    job.detail['modules_submitted'] = list(findings)
                    job.detail['summary'] = sealed.snapshot.payload['summary']
                state.audit_outbox_service.drain_pending()
                job.phase = 'Assessment sealed'
                job.state = 'COMPLETE'
        except Exception:
            # Never expose exception strings: parsers can include local paths and input text.
            job.error = f'{job.phase}: analysis unavailable or backend operation failed. No success was manufactured.'
            for module in MODULES:
                if job.modules[module] == 'RUNNING': job.modules[module] = 'FAILED'
            job.state = 'FAILED'
        finally:
            self.cleanup(job)
