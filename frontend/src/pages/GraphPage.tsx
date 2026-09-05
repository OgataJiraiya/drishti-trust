import { useEffect, useState } from 'react';
import { Focus, Minus, Network, Plus } from 'lucide-react';
import type { AssuranceSummary, Finding, ModuleRun } from '../api/types';
import { StatusBadge } from '../components/ui';

type Node = { id: string; type: string; label: string; detail: string; tone: string; finding?: Finding };

export function GraphPage({ summary, findings, runs, demo }: { summary: AssuranceSummary; findings: Finding[]; runs: ModuleRun[]; demo: boolean }) {
  const demoNodes: Node[] = [
    { id: 'CTR-DEMO-01', type: 'CONTRIBUTOR', label: 'Module Producer', detail: 'Demo contributor identity', tone: 'teal' },
    { id: 'SMP-DEMO-04', type: 'SAMPLE', label: 'Camera Sample', detail: 'Demo sample identity', tone: 'teal' },
    { id: 'DATA-DEMO-02', type: 'DATASET', label: 'Reference Dataset', detail: 'Demo dataset identity', tone: 'teal' },
    { id: 'MODEL-DEMO-04', type: 'MODEL', label: 'Vision Model v4', detail: 'Demo model identity', tone: 'teal' },
    { id: 'INF-DEMO-18', type: 'INFERENCE', label: 'Inference Receipt', detail: 'Demo receipt identity', tone: 'amber' },
    ...(findings[0] ? [{ id: findings[0].finding_id, type: 'FINDING', label: findings[0].category, detail: findings[0].reason, tone: 'red', finding: findings[0] }] : []),
  ];
  const liveNodes: Node[] = [{ id: summary.assessment_id ?? 'GLOBAL', type: 'ASSESSMENT', label: 'Assessment Context', detail: summary.assessment_status ?? 'No active assessment', tone: 'teal' }, ...runs.map((run) => ({ id: run.run_id, type: 'MODULE RUN', label: run.module.replaceAll('_', ' '), detail: `${run.producer} · ${run.authentication.mode}`, tone: run.authentication.authenticated ? 'teal' : 'amber' })), ...findings.map((finding) => ({ id: finding.finding_id, type: 'FINDING', label: finding.category, detail: finding.reason, tone: finding.severity === 'CRITICAL' || finding.severity === 'HIGH' ? 'red' : 'amber', finding }))];
  const nodes = demo ? demoNodes : liveNodes;
  const [selected, setSelected] = useState((demo ? findings[0]?.finding_id : nodes[0]?.id) ?? nodes[0]?.id ?? '');
  useEffect(() => { if (demo && findings[0]?.finding_id) setSelected(findings[0].finding_id); }, [demo, findings]);
  const [zoom, setZoom] = useState(1);
  const node = nodes.find((item) => item.id === selected) ?? nodes[0];
  return <div className="reference-page graph-page">
    <header className="reference-page-header"><div><span>ASSURANCE GRAPH</span><h1>ASSURANCE GRAPH</h1><p>Evidence relationships for this assessment</p></div></header>
    <p className="graph-legend">Assessment ↓ contains Module Run ↓ produced Finding. Unsupported relationships are omitted.</p><div className="graph-layout"><section className="graph-canvas"><div className="graph-toolbar"><button aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(1.4, value + .1))}><Plus /></button><button aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(.7, value - .1))}><Minus /></button><button aria-label="Fit graph" onClick={() => setZoom(1)}><Focus /></button></div>{!demo && runs.length > 0 ? <div className="live-graph" style={{ zoom }}><button title={summary.assessment_id ?? ''} onClick={() => setSelected(summary.assessment_id ?? 'GLOBAL')}>Assessment: {summary.assessment_id ?? 'GLOBAL'}</button>{runs.map(run => <section key={run.run_id}>{run.assessment_id === summary.assessment_id && summary.assessment_id && <p aria-label="Assessment contains module run">↓ contains</p>}<button title={run.run_id} onClick={() => setSelected(run.run_id)}>Module Run: {run.module === 'distribution_shift' ? 'Distribution Shift' : run.module.replaceAll('_', ' ')}<code>{run.run_id}</code></button>{run.authentication.authenticated && run.assessment_id === summary.assessment_id && findings.filter(f => run.finding_ids.includes(f.finding_id) && f.module === run.module).map(f => <div key={f.finding_id}><p aria-label="Module run produced finding">↓ produced</p><button title={f.finding_id} onClick={() => setSelected(f.finding_id)}>Finding: {f.category.replaceAll('_', ' ')}<code>{f.finding_id}</code></button></div>)}</section>)}</div> : !demo && runs.length === 0 ? <div className="graph-empty"><Network /><h2>No complete provenance graph available</h2><p>Loaded records do not provide enough relational fields to display provenance edges.</p></div> : <div className={`graph-nodes ${demo ? 'reference-connected' : ''}`} style={{ transform: `scale(${zoom})` }}>{nodes.map((item, index) => <button key={item.id} className={`graph-node node-${index} tone-${item.tone}${selected === item.id ? ' selected' : ''}`} onClick={() => setSelected(item.id)}><small>{item.type}</small><strong>{item.label.replaceAll('_', ' ')}</strong><code title={item.id}>{item.id}</code></button>)}</div>}</section>
      <aside className="inspector graph-inspector">{node ? <><span className="eyebrow">NODE DETAILS</span><code className="node-id">{node.id}</code><h2>{node.label.replaceAll('_', ' ')}</h2><dl><div><dt>TYPE</dt><dd>{node.type}</dd></div>{node.finding && <><div><dt>CATEGORY</dt><dd>{node.finding.category}</dd></div><div><dt>SEVERITY</dt><dd><StatusBadge value={node.finding.severity} /></dd></div><div><dt>CONFIDENCE</dt><dd>{Math.round(node.finding.confidence * 100)}%</dd></div></>}</dl>{node.finding && <div className="confidence-line"><i style={{ width: `${Math.round(node.finding.confidence * 100)}%` }} /></div>}<section><h3>EVIDENCE TRACE</h3><ol className="evidence-trace"><li>{node.type}</li><li>{node.id}</li><li>Loaded assessment context</li></ol><p>{node.detail}</p></section>{!demo && <p className="inspector-boundary">Unsupported relationships are omitted.</p>}</> : <p>No node selected.</p>}</aside>
    </div>
  </div>;
}
