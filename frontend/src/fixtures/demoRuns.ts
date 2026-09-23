import type { ModuleRun } from '../api/types';

export function demoRuns(id: string | null): ModuleRun[] {
  const modules = ['dataset_integrity', 'model_integrity', 'inference_integrity'] as const;
  return modules.map((module, i) => ({
    run_id: `RUN-DEMO-00${i + 1}`,
    assessment_id: id,
    module,
    producer: `netrava-${module}`,
    producer_version: '1.0.0',
    request_hash: 'a'.repeat(64),
    total_findings: i,
    created_findings: i,
    existing_findings: 0,
    finding_ids: [],
    created_at: '2026-09-03T05:00:00Z',
    authentication: {
      authenticated: true,
      mode: 'ED25519',
      producer_id: `producer-${i + 1}`,
      key_id: `key-${i + 1}`,
      key_fingerprint: 'b'.repeat(64),
      request_hash: 'a'.repeat(64),
      authenticated_at: '2026-09-03T05:00:00Z',
    },
  }));
}
