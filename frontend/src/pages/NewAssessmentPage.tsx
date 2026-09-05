import { useEffect, useState } from 'react';
import type { AssuranceSummary } from '../api/types';
import { useNavigate } from 'react-router-dom';

const BASE = (import.meta.env.VITE_DRISHTI_API_URL ?? '').replace(/\/$/, '');
const roles = [
  ['candidate', 'Model · candidate ONNX', '.onnx', false],
  ['reference', 'Reference Model · optional ONNX', '.onnx', false],
  ['corpus', 'Behavioral Test Inputs · numeric NPY', '.npy', false],
  ['dataset', 'Dataset Evidence · selected images', '.png,.jpg,.jpeg,.bmp,.webp', true],
  ['distribution_reference', 'Distribution Shift · reference images', '.png,.jpg,.jpeg,.bmp,.webp', true],
  ['distribution_current', 'Distribution Shift · current images', '.png,.jpg,.jpeg,.bmp,.webp', true],
  ['representation', 'Distribution Shift · representation pair JSON', '.json', false],
  ['prediction', 'Distribution Shift · prediction pair JSON', '.json', false],
  ['inference', 'Inference Evidence · signed receipt verification JSON', '.json', false],
] as const;
interface Staged { role: string; filename: string; size: number; sha256: string; declared_format: string; registry_matches?: { model_id: string; status: string }[] }
interface Job { assessment_id: string; state: string; phase: string; files: Staged[]; modules: Record<string, string>; detail: Record<string, unknown> & { summary?: AssuranceSummary }; error: string | null }

export function NewAssessmentPage({ demo, onSelect }: { demo: boolean; onSelect: (id: string) => void }) {
  const navigate = useNavigate();
  const [token, setToken] = useState('');
  const [name, setName] = useState('');
  const [version, setVersion] = useState('');
  const [notes, setNotes] = useState('');
  const [execute, setExecute] = useState(false);
  const [contract, setContract] = useState({ input_name: '', output_name: '', layout: '', value_min: '', value_max: '', classification_kind: '', class_axis: '' });
  const [files, setFiles] = useState<Record<string, File[]>>({});
  const [job, setJob] = useState<Job>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [activity, setActivity] = useState('');
  async function request(path: string, method = 'GET', body?: BodyInit, extra?: Record<string, string>) {
    const response = await fetch(`${BASE}/api/intake/${path}`, { method, body, headers: { 'X-Drishti-Intake': token, ...extra } });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `Intake request failed (${response.status})`);
    return result;
  }
  useEffect(() => {
    if (job?.state !== 'RUNNING') return;
    let stopped = false;
    const timer = setInterval(() => {
      void fetch(`${BASE}/api/intake/job`, { headers: { 'X-Drishti-Intake': token } }).then(async response => {
        if (!response.ok) throw new Error('Progress unavailable. Reconnect using the local capability; no fixture fallback is used.');
        const next: Job = await response.json();
        if (!stopped) setJob(next);
      }).catch((reason: Error) => { if (!stopped) setError(reason.message); });
    }, 1000);
    return () => { stopped = true; clearInterval(timer); };
  }, [job?.state, token]);
  const result = job?.detail.summary;
  const chosen = Object.values(files).flat();
  const hasEvidence = Boolean(files.candidate?.length || files.dataset?.length || files.inference?.length || files.representation?.length || files.prediction?.length || (files.distribution_reference?.length && files.distribution_current?.length));
  const validBehavior = !execute || (files.candidate?.length && files.corpus?.length && contract.input_name && contract.output_name && contract.layout && contract.value_min !== '' && contract.value_max !== '' && contract.class_axis !== '');
  async function stage() {
    setBusy(true); setError('');
    try {
      if (chosen.length > 128 || chosen.some(file => file.size > 32 * 1024 * 1024) || chosen.reduce((total, file) => total + file.size, 0) > 128 * 1024 * 1024) throw new Error('Limit: 128 files, 32 MiB each, 128 MiB total.');
      setActivity('Creating local intake job');
      const created: Job = await request('job', 'POST', JSON.stringify({ name, version, notes, behavioral: execute ? { ...contract, execute: true, value_min: Number(contract.value_min), value_max: Number(contract.value_max), class_axis: Number(contract.class_axis), classification_kind: contract.classification_kind || null } : null }), { 'Content-Type': 'application/json' });
      setJob(created);
      for (const [role, selected] of Object.entries(files)) for (const file of selected) {
        setActivity(`Staging ${file.name}`);
        await request(`job/files/${role}`, 'PUT', file, { 'X-Drishti-Filename': file.name, 'Content-Type': 'application/octet-stream' });
      }
      setJob(await request('job')); setActivity('Evidence staged. Review identity before execution.');
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Intake failed'); }
    finally { setBusy(false); }
  }
  async function run() {
    setBusy(true); setError('');
    try { setJob(await request('job/run', 'POST')); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Assessment failed'); }
    finally { setBusy(false); }
  }
  return <div className="reference-page intake-page"><header className="reference-page-header"><div><span>ORGANIZATION EVIDENCE</span><h1>New Assessment</h1><p>Run local integrity checks on organization-provided evidence.</p></div></header>
    {demo ? <p role="status">New Assessment requires live mode. Restart the workstation with demo mode disabled; controlled demo fixtures are not organization evidence.</p> : <>
    <p>Static model analysis does not execute the model. Behavioral execution requires explicit opt-in. Reference designation does not itself prove trust. Digest match proves identity only.</p>
    <fieldset disabled={busy || Boolean(job)}><legend>1. Assessment</legend>
      <label>Local intake capability<input type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} /></label>
      <p>Generate a 30-minute, single-job capability with <code>python scripts/intake_capability.py</code> on this workstation. It is held only in memory.</p>
      <label>Assessment name<input required maxLength={256} value={name} onChange={event => setName(event.target.value)} /></label>
      <label>Version / pipeline label<input maxLength={128} value={version} onChange={event => setVersion(event.target.value)} /></label>
      <label>Notes<textarea maxLength={4096} value={notes} onChange={event => setNotes(event.target.value)} /></label>
      {roles.map(([role, title, accept, multiple]) => <label key={role}>{title}<input type="file" accept={accept} multiple={multiple} onChange={event => setFiles({ ...files, [role]: Array.from(event.target.files ?? []) })} /></label>)}
      <p>Selected images only: no recursive folder ingestion or archives. Dataset checks cover images; labels and contributor risk are not assessed by this intake. Distribution Shift needs both windows; representation and prediction pair JSON must include an explicit descriptor and real reference/current evidence.</p>
      <p>Receipt JSON uses the existing VerifyReceiptRequest contract and the local receipt trust root. Verification does not accept a new event or assess replay acceptance.</p>
      <label><input type="checkbox" checked={execute} onChange={event => setExecute(event.target.checked)} />Explicitly enable bounded BEHAVIORAL EXECUTION</label>
      {execute && <fieldset><legend>Behavioral input contract · do not guess semantics</legend>{(['input_name', 'output_name', 'value_min', 'value_max', 'class_axis'] as const).map(key => <label key={key}>{key.replaceAll('_', ' ')}<input value={contract[key]} onChange={event => setContract({ ...contract, [key]: event.target.value })} /></label>)}<label>Layout<select value={contract.layout} onChange={event => setContract({ ...contract, layout: event.target.value })}><option value="">Select explicit layout</option><option>NCHW</option><option>NHWC</option></select></label><label>Classification kind<select value={contract.classification_kind} onChange={event => setContract({ ...contract, classification_kind: event.target.value })}><option value="">Not classification</option><option>LOGITS</option><option>PROBABILITIES</option></select></label></fieldset>}
      <button disabled={!token || !name.trim() || !hasEvidence || !validBehavior || Boolean(files.corpus?.length && !execute)} onClick={() => void stage()}>STAGE EVIDENCE FOR REVIEW</button>
    </fieldset>
    {activity && <p role="status">{activity}</p>}{error && <p role="alert">{error}</p>}
    {job && <section aria-label="Intake review and progress"><h2>Review / Run Assessment</h2><code>{job.assessment_id}</code>{job.files.map(file => <article key={`${file.role}-${file.sha256}`}><h3>{file.role.replaceAll('_', ' ')} · {file.filename}</h3><p>{file.size} bytes · declared {file.declared_format.toUpperCase()}</p><code title={file.sha256}>SHA-256: {file.sha256}</code>{file.registry_matches && <p>Registry state (identity only): {file.registry_matches.length ? file.registry_matches.map(match => `${match.model_id}: ${match.status}`).join(' · ') : 'No matching registered identity'}</p>}</article>)}
      {job.state === 'STAGING' && <button disabled={busy || job.files.length !== chosen.length} onClick={() => void run()}>RUN INTEGRITY ASSESSMENT</button>}
      <p role="status">{job.state} · {job.phase}</p><dl>{Object.entries(job.modules).map(([module, status]) => <div key={module}><dt>{module === 'distribution_shift' ? 'Distribution Shift' : module.replaceAll('_', ' ')}</dt><dd>{status}</dd></div>)}</dl>
      {job.error && <p role="alert">{job.error}</p>}{job.state !== 'RUNNING' && job.state !== 'COMPLETE' && job.detail.lifecycle !== 'SEALED' && <button disabled={busy} onClick={() => { void request('job', 'DELETE').then(() => { setJob(undefined); setToken(''); setActivity('Staging cleaned. Generate a new capability to start again.'); }).catch((reason: Error) => setError(reason.message)); }}>CANCEL AND CLEAN STAGING</button>}
      {typeof job.detail.audit_durability === 'string' && <p role="status">Audit durability: {String(job.detail.audit_durability)}{job.detail.audit_durability === 'DEGRADED' && ' · Deferred audit delivery needs attention.'}{job.detail.audit_durability === 'DEGRADED' && job.detail.lifecycle === 'SEALED' && ' The sealed assessment result is unchanged.'}</p>}
      {job.detail.lifecycle === 'SEALED' && result && <><p>Lifecycle: {String(job.detail.lifecycle)} · Findings: {String(job.detail.findings)}</p>{result && <dl><dt>Backend assurance</dt><dd>{result.overall.assurance_score === null ? 'UNAVAILABLE' : `${result.overall.assurance_score.toFixed(1)} / 100`}</dd><dt>Score status</dt><dd>{result.overall.score_status}</dd><dt>Coverage</dt><dd>{result.overall.assessment_coverage}</dd><dt>System disposition</dt><dd>{result.overall.disposition}</dd><dt>Audit state</dt><dd>{result.audit_integrity.status}</dd></dl>}<p>Showing the sealed assessment. Only persisted authenticated module evidence contributes to the backend summary.</p><button onClick={() => { onSelect(job.assessment_id); navigate('/'); }}>VIEW ASSESSMENT</button></>}
    </section>}</>}
  </div>;
}
