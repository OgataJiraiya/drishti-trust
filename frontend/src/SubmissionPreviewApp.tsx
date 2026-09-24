import { useState } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { AppShell } from './components/layout/AppShell';
import { demoAssessments, demoScenarios } from './fixtures/demo';
import { demoRuns } from './fixtures/demoRuns';
import { DistributionPage } from './pages/DistributionPage';
import { FindingsPage } from './pages/FindingsPage';
import { OverviewPage } from './pages/OverviewPage';
import { ReportsPage } from './pages/ReportsPage';

type Scenario = 'critical' | 'clean';
const storageKey = 'drishti-preview-scenario';
function isScenario(value: unknown): value is Scenario { return value === 'critical' || value === 'clean'; }
function restoreScenario(): Scenario | null {
  try { const value = sessionStorage.getItem(storageKey); return isScenario(value) ? value : null; }
  catch { return null; }
}

export default function SubmissionPreviewApp() {
  const [scenario, setScenario] = useState<Scenario | null>(restoreScenario);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // Public detail routes always have a fixture context, even without an explicit choice.
  const context = scenario ?? 'critical';
  const summary = demoScenarios[context];
  const showSelector = scenario !== null || ['/findings', '/distribution', '/reports'].includes(pathname);
  function choose(next: Scenario | null, openOverview = false) {
    setScenario(next);
    try {
      if (next) sessionStorage.setItem(storageKey, next);
      else sessionStorage.removeItem(storageKey);
    } catch { /* Storage can be unavailable; in-memory navigation still works. */ }
    if (next === null || openOverview) navigate('/');
  }
  const selector = showSelector && <div className="context"><label htmlFor="demo-scenario">Demo Scenario</label><select id="demo-scenario" value={context} onChange={event => { if (isScenario(event.target.value)) choose(event.target.value); }}><option value="critical">High-Risk Assessment</option><option value="clean">Reference Assessment</option></select></div>;
  const snapshot = demoScenarios.critical;
  const landing = <div className="dossier-landing">
    <section className="dossier-index" aria-labelledby="dossier-title">
      <header className="dossier-intro"><div className="dossier-label">DRISHTI / AI ASSURANCE REVIEW</div><h1 id="dossier-title">Evidence before confidence.</h1><p>Inspect authenticated integrity evidence across data, model, inference and distribution shift before operational trust is granted.</p></header>
      <h2 className="dossier-label">ASSESSMENT INDEX</h2>
      {([
        ['critical', '01', 'High-Risk Pipeline', 'Authenticated evidence with model, inference and distribution-shift concerns.'],
        ['clean', '02', 'Reference Pipeline', 'Higher-assurance reference posture for comparison.'],
      ] as const).map(([key, number, title, description]) => <article className={'dossier-row dossier-' + key} key={key} aria-label={title}>
        <span className="dossier-number">{number}</span><div><h3>{title}</h3><p>{description}</p><button aria-label={'Open assessment: ' + title} onClick={() => choose(key, true)}>Open assessment <ArrowRight size={15} aria-hidden="true"/></button></div><span className="dossier-status">{demoScenarios[key].overall.disposition}</span>
      </article>)}
      <p className="dossier-note">Browser-local demonstration fixtures. No live backend execution or organization evidence.</p>
    </section>
    <aside className="dossier-snapshot" aria-label="Assurance snapshot">
      <h2 className="dossier-label">ASSURANCE SNAPSHOT</h2>
      <dl><div className="snapshot-score"><dt>ASSURANCE</dt><dd>{snapshot.overall.assurance_score?.toFixed(1)} <span>/ 100</span></dd></div><div><dt>DISPOSITION</dt><dd className="snapshot-disposition">{snapshot.overall.disposition}</dd></div><div><dt>COVERAGE</dt><dd>{Math.round(snapshot.overall.assessment_coverage * 100)}%</dd></div><div><dt>AUDIT CHAIN</dt><dd>{snapshot.audit_integrity.status}</dd></div><div><dt>EVIDENCE MODEL</dt><dd>Authenticated</dd></div></dl>
      <div className="dossier-fixture"><span>DEMO DATA</span><p>Controlled assessment fixture</p></div>
    </aside>
  </div>;

  return <AppShell assessments={demoAssessments} selected={showSelector ? summary.assessment_id ?? '' : ''} onSelect={() => {}} backend="OFFLINE" demo previewScenario={selector} onResetPreview={() => choose(null)}>
    <div className="submission-preview-label">SIH Submission Preview · Core Integrity Assurance Workflow</div>
    <div className="submission-preview-content">
      <Routes>
        <Route path="/" element={scenario === null ? landing : <><div className="dossier-context"><span>{context === 'critical' ? 'HIGH-RISK ASSESSMENT' : 'REFERENCE ASSESSMENT'}</span><button onClick={() => choose(null)}>Change assessment</button></div><OverviewPage summary={summary} runs={demoRuns(summary.assessment_id)} executionAvailable /></>} />
        <Route path="/findings" element={<FindingsPage key={context} findings={summary.latest_findings} />} />
        <Route path="/distribution" element={<DistributionPage key={context} summary={summary} findings={summary.latest_findings} demo />} />
        <Route path="/reports" element={<ReportsPage summary={summary} findings={summary.latest_findings} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  </AppShell>;
}
