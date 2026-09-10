import { ChevronDown, Database, Printer, ScanLine, ShieldCheck } from 'lucide-react';
import type { AssuranceSummary, Finding } from '../api/types';
import { AssuranceHero } from '../components/assurance/Assurance';
import { StatusBadge } from '../components/ui';

export function ReportsPage({ summary, findings, limited = false }: { summary: AssuranceSummary; findings: Finding[]; limited?: boolean }) {
  return <div className="reference-page report-page">
    <header className="reference-page-header"><div><span>ASSURANCE REPORT</span><h1>Assessment assurance summary</h1><p><code>{summary.assessment_id ?? 'GLOBAL CONTEXT'}</code> · Generated {new Date(summary.generated_at).toLocaleString()}</p></div><button className="print-button" onClick={() => window.print()}><Printer />PRINT / SAVE AS PDF</button></header>
    <AssuranceHero summary={summary} />
    {limited && <p role="status">Evidence loading is incomplete; this report contains only a subset of findings.</p>}
    <section className="reference-section executive-summary"><span>EXECUTIVE SUMMARY</span><p>{summary.overall.reason}</p></section>
    <section className="reference-section attention-list"><span>FINDINGS REQUIRING ATTENTION ({findings.length} FINDINGS)</span>{findings.map((finding) => <div className={`report-finding severity-${finding.severity.toLowerCase()}`} key={finding.finding_id}><code>{finding.finding_id}</code><strong>{finding.category.replaceAll('_', ' ')}</strong><StatusBadge value={finding.severity} /><StatusBadge value={finding.recommendation} /></div>)}</section>
    <section className="reference-section pipeline-reference"><span>EVIDENCE TRACE PATH</span><div><b><Database />DATA</b><i>→</i><b><ScanLine />MODEL</b><i>→</i><b><ShieldCheck />INFERENCE</b><i>→</i><b>FINDING</b><i>→</i><b>AUDIT</b></div><small>Conceptual assurance pipeline only.</small></section>
    <details className="reference-limitations"><summary><span>LIMITATIONS <small>{summary.limitations.length}</small></span><ChevronDown /></summary><ul>{summary.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></details>
  </div>;
}
