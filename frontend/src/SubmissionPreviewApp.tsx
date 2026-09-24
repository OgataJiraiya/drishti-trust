import { useState } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { Activity, FileText, MousePointerClick, ShieldAlert, ShieldCheck } from 'lucide-react';
import { AppShell } from './components/layout/AppShell';
import { demoAssessments, demoScenarios } from './fixtures/demo';
import { demoRuns } from './fixtures/demoRuns';
import { DistributionPage } from './pages/DistributionPage';
import { FindingsPage } from './pages/FindingsPage';
import { OverviewPage } from './pages/OverviewPage';
import { ReportsPage } from './pages/ReportsPage';

type Scenario = 'critical' | 'clean';

export default function SubmissionPreviewApp() {
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const navigate = useNavigate();
  const summary = scenario ? demoScenarios[scenario] : null;
  function choose(next: Scenario | null) { setScenario(next); navigate('/'); }
  const selector = scenario && <div className="context"><label htmlFor="demo-scenario">Demo Scenario</label><select id="demo-scenario" value={scenario} onChange={event => choose(event.target.value === 'clean' ? 'clean' : 'critical')}><option value="critical">High-Risk Assessment</option><option value="clean">Clean Reference Assessment</option></select></div>;

  const landing = <div className="preview-landing">
    <section className="preview-hero" aria-labelledby="preview-title"><div className="preview-eyebrow">SIH 2026 · INTERACTIVE PROTOTYPE</div><h1 id="preview-title">Verify trust before deployment</h1><p>Explore how DRISHTI turns authenticated AI-pipeline evidence into explainable assurance, integrity findings and operational recommendations.</p><div className="preview-trust"><span>AUTHENTICATED EVIDENCE MODEL</span><span>NO CLOUD DEPENDENCY</span></div></section>
    <section className="preview-scenarios" aria-labelledby="scenario-title">
      <header><h2 id="scenario-title">Choose a demo assessment</h2><span className="badge tone-demo">DEMO DATA</span><small>Controlled demonstration data</small></header>
      <div className="preview-scenario-grid">
        <article className="preview-scenario high-risk"><div className="preview-profile"><ShieldAlert aria-hidden="true"/><span>HIGH-RISK PROFILE</span></div><h3>Compromised AI Pipeline</h3><p>Inspect an authenticated assurance snapshot containing model, inference and distribution-shift concerns.</p><div className="preview-features">{['CRITICAL FINDING', 'MODEL INTEGRITY', 'REPLAY SIGNAL', 'QUARANTINE'].map(text => <span key={text}>{text}</span>)}</div><button onClick={() => choose('critical')}>Review High-Risk Assessment</button></article>
        <article className="preview-scenario reference"><div className="preview-profile"><ShieldCheck aria-hidden="true"/><span>REFERENCE PROFILE</span></div><h3>Clean Reference Pipeline</h3><p>Inspect a controlled reference assessment with stronger integrity posture.</p><div className="preview-features">{[demoScenarios.clean.overall.assurance_score?.toFixed(1) + ' SCORE', 'AUDIT ' + demoScenarios.clean.audit_integrity.status, 'FULL COVERAGE', demoScenarios.clean.overall.disposition].map(text => <span key={text}>{text}</span>)}</div><button onClick={() => choose('clean')}>Review Clean Assessment</button></article>
      </div>
      <ol className="preview-steps" aria-label="How it works">{([[MousePointerClick, 'Select assessment'], [Activity, 'Review assurance'], [ShieldCheck, 'Inspect findings'], [FileText, 'Export report']] as const).map(([Icon, text], index) => <li key={text}><Icon aria-hidden="true"/><div><span>0{index + 1}</span><strong>{text}</strong></div></li>)}</ol>
      <p className="preview-provenance">Browser-local demo fixtures · No live backend execution or organization evidence.</p>
    </section>
  </div>;

  return <AppShell assessments={demoAssessments} selected={summary?.assessment_id ?? ''} onSelect={() => {}} backend="OFFLINE" demo previewScenario={selector}>
    <div className="submission-preview-label">SIH Submission Preview · Core Integrity Assurance Workflow</div>
    <div className="submission-preview-content">
      {summary ? <Routes>
        <Route path="/" element={<>{scenario && <div className="preview-context"><span>{scenario === 'critical' ? 'High-risk demo loaded. Explore Findings, Distribution Shift and Reports.' : 'Reference demo loaded. Explore assurance details and reporting.'}</span><button onClick={() => choose(null)}>Change demo scenario</button></div>}<OverviewPage summary={summary} runs={demoRuns(summary.assessment_id)} executionAvailable /></>} />
        <Route path="/findings" element={<FindingsPage key={scenario} findings={summary.latest_findings} />} />
        <Route path="/distribution" element={<DistributionPage key={scenario} summary={summary} findings={summary.latest_findings} demo />} />
        <Route path="/reports" element={<ReportsPage summary={summary} findings={summary.latest_findings} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes> : <Routes><Route path="/" element={landing}/><Route path="*" element={<Navigate to="/" replace />}/></Routes>}
    </div>
  </AppShell>;
}
