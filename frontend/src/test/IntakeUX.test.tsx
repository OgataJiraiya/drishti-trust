import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it } from 'vitest';
import { demoScenarios } from '../fixtures/demo';
import { OverviewPage } from '../pages/OverviewPage';
import { HistoricalContext } from '../components/ui/Help';
import { Evidence } from '../components/findings/Evidence';
import { DistributionPage } from '../pages/DistributionPage';
import { NewAssessmentPage } from '../pages/NewAssessmentPage';
import { SystemPage } from '../pages/SystemPage';
import { GraphPage } from '../pages/GraphPage';
import type { ModuleRun } from '../api/types';

afterEach(cleanup);
const summary = demoScenarios.critical;
it('shows exact backend 48.3 and preserves unavailable', () => {
  const { rerender } = render(<MemoryRouter><OverviewPage summary={{ ...summary, overall: { ...summary.overall, assurance_score: 48.3 } }} /></MemoryRouter>);
  expect(screen.getByText('48.3 / 100')).toBeVisible();
  expect(screen.queryByText('48%')).not.toBeInTheDocument();
  rerender(<MemoryRouter><OverviewPage summary={{ ...summary, overall: { ...summary.overall, assurance_score: null } }} /></MemoryRouter>);
  expect(screen.getByText('UNAVAILABLE')).toBeVisible();
});
it('labels only the authenticated selected sealed assessment as historical', () => {
  const { rerender } = render(<HistoricalContext selected="HISTORY" summary={{ ...summary, assessment_id: 'HISTORY', assessment_status: 'SEALED' }} />);
  expect(screen.getByRole('status')).toHaveTextContent('HISTORY · SEALED');
  expect(screen.queryByText(/No active assessment/)).not.toBeInTheDocument();
  rerender(<HistoricalContext selected="HISTORY" summary={{ ...summary, assessment_id: 'HISTORY', assessment_status: 'ACTIVE' }} />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});
it('provides keyboard-accessible module and decision help', async () => {
  render(<MemoryRouter><OverviewPage summary={summary} /></MemoryRouter>);
  const module = screen.getByLabelText('About model integrity');
  module.focus(); expect(module).toHaveFocus();
  // jsdom does not implement native details keyboard activation; browser E2E covers Enter.
  await userEvent.click(module);
  expect(screen.getByText(/Checks model artifact identity/)).toBeVisible();
  await userEvent.click(screen.getByText('Status and decision semantics'));
  expect(screen.getByText(/This is a module status, not a Finding recommendation/)).toBeVisible();
  expect(screen.getByText(/does not itself reject the entire pipeline/)).toBeVisible();
  expect(screen.getByText('Distribution Shift')).toBeVisible();
  expect(screen.queryByText('DRIFT')).not.toBeInTheDocument();
});
it('summarizes recognized evidence and preserves exact raw evidence on expansion', async () => {
  const evidence = ['pattern_code=BROAD_MULTILAYER_SHIFT', 'changed_layers=IMAGE_STATISTICAL,REPRESENTATION,PREDICTION_OUTPUT', 'unrecognized=keep me'];
  render(<Evidence evidence={evidence} />);
  expect(screen.getByText('broad multilayer shift')).toBeVisible();
  expect(screen.getByText('Image / statistical · Representation · Prediction output')).toBeVisible();
  const technical = screen.getByText(/TECHNICAL EVIDENCE/).parentElement!;
  expect(technical).not.toHaveAttribute('open');
  await userEvent.click(screen.getByText(/TECHNICAL EVIDENCE/));
  expect(technical).toHaveAttribute('open');
  for (const raw of evidence) expect(technical).toHaveTextContent(raw);
});
it('links Distribution Shift to filtered authenticated findings', () => {
  render(<MemoryRouter><DistributionPage summary={summary} findings={summary.latest_findings} demo={false} /></MemoryRouter>);
  expect(screen.getByRole('link', { name: 'VIEW AUTHENTICATED EVIDENCE' })).toHaveAttribute('href', '/findings?module=distribution_shift');
  expect(screen.getByText(/visualization is not stored/)).toBeVisible();
});
it('navigates System to New Assessment and requires explicit evidence before staging', () => {
  const { unmount } = render(<SystemPage summary={summary} backend="ONLINE" runs={[]} />);
  expect(screen.getByRole('link', { name: 'RUN ASSESSMENT' })).toHaveAttribute('href', '/new-assessment');
  unmount();
  render(<MemoryRouter><NewAssessmentPage demo={false} onSelect={() => {}} /></MemoryRouter>);
  expect(screen.getByRole('heading', { name: 'New Assessment' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'STAGE EVIDENCE FOR REVIEW' })).toBeDisabled();
  expect(screen.getByRole('checkbox')).not.toBeChecked();
});
it('draws only actual run membership and finding identities', () => {
  const finding = summary.latest_findings[0];
  const run: ModuleRun = { run_id: 'RUN-A', assessment_id: 'A', module: finding.module, producer: 'P', producer_version: '1', request_hash: '', total_findings: 1, created_findings: 1, existing_findings: 0, finding_ids: [finding.finding_id], created_at: '', authentication: { authenticated: true, mode: 'ED25519', producer_id: 'P', key_id: 'K', key_fingerprint: '', request_hash: '', authenticated_at: '' } };
  const { rerender } = render(<GraphPage summary={{ ...summary, assessment_id: 'A' }} findings={[finding]} runs={[run]} demo={false} />);
  expect(screen.getByLabelText('Assessment contains module run')).toBeVisible();
  expect(screen.getByLabelText('Module run produced finding')).toBeVisible();
  rerender(<GraphPage summary={{ ...summary, assessment_id: 'A' }} findings={[finding]} runs={[{ ...run, assessment_id: 'OTHER' }]} demo={false} />);
  expect(screen.queryByLabelText('Assessment contains module run')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Module run produced finding')).not.toBeInTheDocument();
});
