import { AlertTriangle, ArrowRight, Database, ScanLine, ShieldCheck, Waves } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { AssuranceSummary, Assessment, ModuleKey } from '../api/types';
import { StatusBadge } from '../components/ui';

const moduleIcons = { dataset_integrity: Database, model_integrity: ShieldCheck, inference_integrity: ScanLine, distribution_shift: Waves };
const moduleOrder: ModuleKey[] = ['dataset_integrity', 'model_integrity', 'inference_integrity', 'distribution_shift'];

export function OverviewPage({ summary }: { summary: AssuranceSummary; current?: Assessment }) {
  const top = summary.latest_findings[0];
  return <div className="overview-workspace overview-page">
    <section className="overview-assurance" aria-label="Overall assurance"><span>OVERALL ASSURANCE</span><div role="img" aria-label={summary.overall.assurance_score === null ? 'Assurance score unavailable' : `Backend assurance score ${summary.overall.assurance_score.toFixed(1)} out of 100`}><strong>{summary.overall.assurance_score === null ? '—' : `${Math.round(summary.overall.assurance_score)}%`}</strong><StatusBadge value={summary.overall.disposition} /></div></section>
    <section className="overview-modules" aria-label="Assurance modules">{moduleOrder.map((key) => { const item = summary.modules[key]; const Icon = moduleIcons[key]; return <article key={key} className={`overview-module module-${item.status.toLowerCase()}`}><span>{key === 'distribution_shift' ? 'DRIFT' : item.display_name.toUpperCase()}</span><strong><Icon />{item.status.replaceAll('_', ' ')}</strong><small>{item.availability === 'UNKNOWN' ? item.availability_reason : `${item.finding_count} ${item.finding_count === 1 ? 'finding' : 'findings'} · ${item.availability}`}</small></article>; })}</section>
    <section className="overview-attention"><div><AlertTriangle /><p><strong>Findings Requiring Attention: {summary.trusted_finding_count}</strong><code>Top: {top ? `${top.finding_id} · ${top.category.replaceAll('_', ' ')}` : 'No authenticated finding supplied'}</code></p></div><Link to="/findings">Investigate Findings <ArrowRight /></Link></section>
    <aside className="semantic-context" aria-label="Evidence context"><span>{summary.trusted_finding_count} authenticated · {summary.excluded_untrusted_finding_count} excluded</span>{top && <span>{top.category.replaceAll('_', ' ')}</span>}<span>Coverage {Math.round(summary.overall.assessment_coverage * 100)}%</span><span>{summary.overall.score_status}</span><span>Audit {summary.audit_integrity.status} · {summary.audit_integrity.records_checked === null ? 'Records unavailable' : `${summary.audit_integrity.records_checked} records checked`}</span>{summary.audit_integrity.first_broken_audit_id && <span>First broken record: {summary.audit_integrity.first_broken_audit_id}</span>}{summary.limitations.map((item) => <span key={item}>{item}</span>)}</aside>
  </div>;
}
