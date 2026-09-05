#!/usr/bin/env python3
"""Offline D1-D6 scenarios with optional existing-SDK loopback submission."""
from __future__ import annotations
import argparse,json,os,sys
from time import perf_counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from drishti_sdk import DrishtiClient
from modules.distribution_shift import DistributionShiftFinalOrchestrator,DistributionShiftRunBuilder,render_final_result

def public_pem(key):return key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
def main():
    p=argparse.ArgumentParser();p.add_argument("--scenario",choices=("clean","image-shift","representation-shift","output-shift","broad-shift","incomplete"),default="clean");p.add_argument("--all",action="store_true");p.add_argument("--signed",action="store_true");p.add_argument("--base-url",default="http://127.0.0.1:8000");p.add_argument("--assessment-id",default="DRIFT-D6-DEMO");p.add_argument("--admin-token",default=os.getenv("DRISHTI_ADMIN_BEARER_TOKEN"));args=p.parse_args()
    scenarios=("clean","image-shift","representation-shift","output-shift","broad-shift","incomplete") if args.all else (args.scenario,)
    for scenario in scenarios:
        result=DistributionShiftFinalOrchestrator().run_offline(scenario);payload=result.to_dict()
        if args.signed:
            if result.finding_count==0:payload["backend_submission_status"]="NO_FINDINGS_TO_SUBMIT";payload["limitations"]+= ("Frozen ModuleRunSubmission v1 requires at least one Finding; no backend call was made.",)
            else:
                if not args.admin_token:p.error("signed shifted scenarios require DRISHTI_ADMIN_BEARER_TOKEN or --admin-token")
                with DrishtiClient(args.base_url,admin_token=args.admin_token) as client:
                    client.health();client.create_assessment(args.assessment_id,"Distribution Shift D6 final demo");producer="distribution-shift-d6-demo";key_id="distribution-shift-d6-key";key=Ed25519PrivateKey.generate();client.register_producer(producer,"Distribution Shift D6", "distribution_shift");client.register_producer_key(producer,key_id,public_pem(key));client.activate_assessment(args.assessment_id)
                    builder=DistributionShiftRunBuilder(client)
                    from modules.distribution_shift.demo import build_interpretation_for_scenario
                    interpretation=build_interpretation_for_scenario(scenario)[3];findings=builder.map_findings(interpretation);run=builder.build_run(assessment_id=args.assessment_id,producer=producer,producer_version="1.0.0",findings=findings,run_id=f"D6-{args.assessment_id}-{scenario}")
                    started=perf_counter();first=builder.submit_signed_run(run=run,key_id=key_id,bundle_id=interpretation.bundle_id,private_key=key);submission_ms=(perf_counter()-started)*1000;replay=builder.submit_signed_run(run=run,key_id=key_id,bundle_id=interpretation.bundle_id,private_key=key);stored=client.get_run(run.run_id);summary=client.get_summary(args.assessment_id);audit=client.verify_audit();client.seal_assessment(args.assessment_id);snapshot=client.verify_snapshot(args.assessment_id);client.drain_outbox();checkpoint=client.create_checkpoint()["bundle"];checkpoint_status=client.verify_checkpoint(checkpoint);outbox=client.outbox_status()
                    payload.update({"backend_submission_status":first.backend_result,"signed_submission_latency_ms":round(submission_ms,3),"backend_replay_status":replay.backend_result,"backend_run_id":run.run_id,"backend_authenticated":stored["authentication"]["authenticated"],"backend_authentication_mode":stored["authentication"]["mode"],"backend_producer_id":stored["authentication"]["producer_id"],"backend_key_id":stored["authentication"]["key_id"],"backend_summary":summary,"audit_status":audit["status"],"snapshot_status":snapshot["status"],"checkpoint_status":checkpoint_status["status"],"outbox_healthy":outbox["healthy"],"outbox_pending_count":outbox["pending_count"]})
        print(render_final_result(result));print(json.dumps(payload,sort_keys=True,allow_nan=False))
    return 0
if __name__=="__main__":raise SystemExit(main())
