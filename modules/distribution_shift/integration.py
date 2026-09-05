"""Thin reuse of the existing SDK run/sign/submit pipeline for D6."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from backend.schemas.evidence import Finding
from backend.schemas.integration import ModuleRunSubmission
from drishti_sdk import DistributionShiftAdapter,DrishtiClient
from .findings import DistributionShiftFindingMapper,DistributionShiftFindingMappingPolicy
from .interpretation_models import MultiSignalInterpretationReport

@dataclass(frozen=True)
class DistributionShiftIntegrationResult:
    finding_count:int;run_id:str;assessment_id:str;bundle_id:str;backend_result:str

class DistributionShiftRunBuilder:
    def __init__(self,client:DrishtiClient,policy:DistributionShiftFindingMappingPolicy|None=None):
        if not isinstance(client,DrishtiClient):raise TypeError("client must be DrishtiClient")
        self.client=client;self.adapter=DistributionShiftAdapter(client);self.mapper=DistributionShiftFindingMapper(self.adapter,policy)
    def map_findings(self,report:MultiSignalInterpretationReport):return self.mapper.map(report)
    def build_run(self,*,assessment_id:str,producer:str,producer_version:str|None,findings:list[Finding],run_id:str|None=None):
        if any(str(f.module)!="distribution_shift" for f in findings):raise ValueError("every Finding must belong to distribution_shift")
        return self.client.build_run(module="distribution_shift",assessment_id=assessment_id,producer=producer,producer_version=producer_version,findings=findings,run_id=run_id)
    def sign_run(self,run:ModuleRunSubmission,*,private_key:Ed25519PrivateKey|None=None,private_key_path:str|Path|None=None):return self.client.sign_run(run,private_key=private_key,private_key_path=private_key_path)
    def submit_signed_run(self,*,run:ModuleRunSubmission,key_id:str,bundle_id:str,private_key:Ed25519PrivateKey|None=None,private_key_path:str|Path|None=None):
        if any(f.asset_id!=bundle_id for f in run.findings):raise ValueError("bundle identity must match every mapped Finding")
        response=self.client.submit_signed_run(run=run,key_id=key_id,private_key=private_key,private_key_path=private_key_path)
        return DistributionShiftIntegrationResult(len(run.findings),run.run_id,run.assessment_id or "",bundle_id,str(response.get("result","UNKNOWN")))
