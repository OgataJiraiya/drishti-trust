import { useEffect, useState } from 'react';
import type { AssuranceSummary, Finding, ModuleRun, Snapshot, SnapshotVerification } from '../api/types';
import { fetchAssessmentFindings, fetchRuns, fetchSnapshot, fetchSnapshotVerification } from '../api/workspaces';

export function useWorkspaceData(summary?: AssuranceSummary, demo = false) {
  const [findings, setFindings] = useState<Finding[]>([]);
  const [runs, setRuns] = useState<ModuleRun[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [verification, setVerification] = useState<SnapshotVerification>();
  const [loading, setLoading] = useState(true);
  const [runScope, setRunScope] = useState<AssuranceSummary>();
  const [executionAvailable, setExecutionAvailable] = useState(false);
  const [detailUnavailable, setDetailUnavailable] = useState(true);
  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true); setRuns([]); setExecutionAvailable(false); setRunScope(summary);
      setSnapshot(undefined); setVerification(undefined); setDetailUnavailable(true);
      setFindings(summary?.latest_findings ?? []);
      if (!summary) { setLoading(false); return; }
      if (demo) {
        setRuns(demoRuns(summary.assessment_id)); setExecutionAvailable(true);
        setDetailUnavailable(false); setLoading(false); return;
      }
      const id = summary.assessment_id;
      if (id) {
        try {
          const result = await fetchRuns(id);
          if (!active) return;
          setRuns(result.items); setExecutionAvailable(result.total === result.items.length);
        } catch { /* Execution metadata availability is separate from findings. */ }
        try {
          const first = await fetchAssessmentFindings(id, 1);
          if (!active) return;
          const all = [...first.items];
          // Freeze the page count: never chase a continuously growing history.
          for (let page = 2; page <= Math.ceil(first.total / 100); page++) {
            const next = await fetchAssessmentFindings(id, page);
            if (!active) return;
            if (next.total !== first.total) throw new Error('Evidence changed during loading');
            all.push(...next.items);
          }
          if (all.length !== first.total || new Set(all.map(f => f.finding_id)).size !== first.total)
            throw new Error('Evidence pagination was incomplete');
          setFindings(all); setDetailUnavailable(false);
        } catch { if (active) setDetailUnavailable(true); }
        if (!active) return;
        if (summary.assessment_status === 'SEALED') {
          try {
            const [s, v] = await Promise.all([fetchSnapshot(id), fetchSnapshotVerification(id)]);
            if (active) { setSnapshot(s); setVerification(v); }
          } catch { if (active) setDetailUnavailable(true); }
        }
      }
      if (active) setLoading(false);
    }
    void load();
    return () => { active = false; };
  }, [summary, demo]);
  const current = runScope === summary;
  return { findings: current ? findings : summary?.latest_findings ?? [], runs: current ? runs : [],
    snapshot: current ? snapshot : undefined, verification: current ? verification : undefined,
    loading: !current || loading, detailUnavailable: !current || detailUnavailable,
    executionAvailable: current && executionAvailable };
}
function demoRuns(id:string|null):ModuleRun[]{const modules=['dataset_integrity','model_integrity','inference_integrity']as const;return modules.map((module,i)=>({run_id:`RUN-DEMO-00${i+1}`,assessment_id:id,module,producer:`netrava-${module}`,producer_version:'1.0.0',request_hash:'a'.repeat(64),total_findings:i,created_findings:i,existing_findings:0,finding_ids:[],created_at:'2026-09-03T05:00:00Z',authentication:{authenticated:true,mode:'ED25519',producer_id:`producer-${i+1}`,key_id:`key-${i+1}`,key_fingerprint:'b'.repeat(64),request_hash:'a'.repeat(64),authenticated_at:'2026-09-03T05:00:00Z'}}))}
