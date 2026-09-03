import { CheckCircle2, Play, ServerCog } from 'lucide-react';
import type { Assessment, AssuranceSummary, BackendState, ModuleKey, ModuleRun, Snapshot, SnapshotVerification } from '../api/types';
import { StatusBadge } from '../components/ui';

const modules: ModuleKey[] = ['dataset_integrity', 'model_integrity', 'inference_integrity', 'distribution_shift'];

export function SystemPage({ summary, current, backend, runs, snapshot, verification }: { summary: AssuranceSummary; current?: Assessment; backend: BackendState; runs: ModuleRun[]; snapshot?: Snapshot; verification?: SnapshotVerification }) {
  return <div className="reference-page system-page">
    <header className="reference-page-header"><div><span>SYSTEM / RUN STATUS</span><h1>SYSTEM / RUN STATUS</h1><p>Assessment execution status</p></div><button className="run-assessment" disabled title="Assessment execution is not enabled in this read-only build"><Play />RUN ASSESSMENT</button></header>
    <div className="system-reference-grid"><section>
      <div className="system-state"><ServerCog /><span>CURRENT ASSESSMENT STATE</span><strong>{summary.assessment_status ?? 'NO ACTIVE ASSESSMENT'}</strong><p>Last verified {new Date(summary.generated_at).toLocaleString()} · Assessment ID <code>{summary.assessment_id ?? 'UNAVAILABLE'}</code></p></div>
      <div className="module-status-reference"><span>ASSURANCE MODULE STATUS</span><div>{modules.map((module) => { const run = runs.find((item) => item.module === module); return <article key={module}><div><strong>{module === 'distribution_shift' ? 'DRIFT' : module.replaceAll('_', ' ')}</strong><small>{run ? <code>{run.run_id}</code> : 'No signed run attached'}</small></div><span>{run ? 'RUN PRESENT' : 'NOT SUBMITTED'}</span>{run && <small>{run.producer} · {run.total_findings} findings · {run.authentication.authenticated ? 'AUTHENTICATED' : 'UNAVAILABLE'}</small>}</article>; })}</div></div>
    </section><aside className="run-summary"><span>RUN SUMMARY</span><dl><div><dt>ASSESSMENT ID</dt><dd><code>{summary.assessment_id ?? 'UNAVAILABLE'}</code></dd></div><div><dt>RUN STATUS</dt><dd>{summary.assessment_status ?? 'UNAVAILABLE'}</dd></div><div><dt>LAST VERIFIED</dt><dd>{new Date(summary.generated_at).toLocaleString()}</dd></div><div><dt>MODE</dt><dd>{summary.trust_scope}</dd></div><div><dt>BACKEND</dt><dd><StatusBadge value={backend} /></dd></div><div><dt>AUDIT</dt><dd><StatusBadge value={summary.audit_integrity.status} /></dd></div></dl>{summary.assessment_status === 'SEALED' && <div className="snapshot-reference"><span>SEALED SNAPSHOT</span>{snapshot ? <><CheckCircle2 /><strong>SNAPSHOT AVAILABLE</strong><code>{snapshot.summary_hash}</code><StatusBadge value={verification?.status ?? 'UNAVAILABLE'} /></> : <p>Snapshot detail unavailable.</p>}</div>}{current?.name && <small className="assessment-name">{current.name}</small>}</aside></div>
  </div>;
}
