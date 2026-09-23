import { useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
import { demoAssessments, getDemoSummary } from './fixtures/demo';
import { demoRuns } from './fixtures/demoRuns';
import { DistributionPage } from './pages/DistributionPage';
import { FindingsPage } from './pages/FindingsPage';
import { OverviewPage } from './pages/OverviewPage';
import { ReportsPage } from './pages/ReportsPage';

export default function SubmissionPreviewApp() {
  const [selected, setSelected] = useState(demoAssessments[0].assessment_id);
  const summary = getDemoSummary(selected);
  const findings = summary.latest_findings;
  const current = demoAssessments.find((assessment) => assessment.assessment_id === selected);

  return <AppShell assessments={demoAssessments} selected={selected} onSelect={setSelected} backend="OFFLINE" demo>
    <div className="submission-preview-label">SIH Submission Preview · Core Integrity Assurance Workflow</div>
    <Routes>
      <Route path="/" element={<OverviewPage summary={summary} current={current} runs={demoRuns(summary.assessment_id)} executionAvailable />} />
      <Route path="/findings" element={<FindingsPage findings={findings} />} />
      <Route path="/distribution" element={<DistributionPage summary={summary} findings={findings} demo />} />
      <Route path="/reports" element={<ReportsPage summary={summary} findings={findings} />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  </AppShell>;
}
