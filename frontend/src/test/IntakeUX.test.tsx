import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { demoScenarios } from '../fixtures/demo';
import { OverviewPage } from '../pages/OverviewPage';
import { HistoricalContext } from '../components/ui/Help';
import { Evidence } from '../components/findings/Evidence';
import { DistributionPage } from '../pages/DistributionPage';
import { NewAssessmentPage } from '../pages/NewAssessmentPage';
import { SystemPage } from '../pages/SystemPage';
import { GraphPage } from '../pages/GraphPage';
import type { ModuleRun } from '../api/types';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
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

it.each(['COMPLETE', 'FAILED'])('shows sealed %s intake evidence and degraded audit delivery', async state => {
  const file = { role: 'candidate', filename: 'candidate.onnx', size: 5, sha256: 'abc', declared_format: 'onnx' };
  const staged = { assessment_id: 'RECOVERY-A', state: 'STAGING', phase: 'Staged', files: [file], modules: { model_integrity: 'AWAITING SUBMISSION' }, detail: {}, error: null };
  const final = { ...staged, state, phase: 'Assessment sealed', modules: { model_integrity: state === 'COMPLETE' ? 'COMPLETE' : 'NOT SUBMITTED' }, detail: { lifecycle: 'SEALED', summary, findings: 0, audit_durability: 'DEGRADED' }, error: state === 'FAILED' ? 'Partial assessment sealed' : null };
  const replies = [{ ...staged, files: [] }, file, staged, final];
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => ({ ok: true, json: async () => replies.shift() })));
  const onSelect = vi.fn();
  render(<MemoryRouter><NewAssessmentPage demo={false} onSelect={onSelect} /></MemoryRouter>);
  await userEvent.type(screen.getByLabelText('Local intake capability'), 'local-capability');
  await userEvent.type(screen.getByLabelText('Assessment name'), 'Recovery');
  await userEvent.upload(screen.getByLabelText('Model · candidate ONNX'), new File(['model'], 'candidate.onnx'));
  await userEvent.click(screen.getByRole('button', { name: 'STAGE EVIDENCE FOR REVIEW' }));
  await screen.findByText('Evidence staged. Review identity before execution.');
  await userEvent.click(screen.getByRole('button', { name: 'RUN INTEGRITY ASSESSMENT' }));
  expect(await screen.findByText(/Audit durability: DEGRADED/)).toBeVisible();
  expect(screen.getByText(/Lifecycle: SEALED/)).toBeVisible();
  if (state === 'FAILED') expect(screen.getByRole('alert')).toHaveTextContent('Partial assessment sealed');
  else expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'VIEW ASSESSMENT' }));
  expect(onSelect).toHaveBeenCalledWith('RECOVERY-A');
});

it('distinguishes authenticated zero-Finding execution from no submission without changing assurance', () => {
  const zero = structuredClone(demoScenarios.unknown);
  zero.assessment_id = 'ZERO';
  const run: ModuleRun = { run_id: 'ZERO-RUN', assessment_id: 'ZERO', module: 'model_integrity', producer: 'P', producer_version: '1', request_hash: '', total_findings: 0, created_findings: 0, existing_findings: 0, finding_ids: [], created_at: '', authentication: { authenticated: true, mode: 'ED25519', producer_id: 'P', key_id: 'K', key_fingerprint: '', request_hash: '', authenticated_at: '' } };
  const { rerender } = render(<MemoryRouter><OverviewPage summary={zero} runs={[run]} executionAvailable /></MemoryRouter>);
  expect(screen.getByText('Execution: Authenticated assessment completed')).toBeVisible();
  expect(screen.getByText(/Absence of Findings does not establish model safety/)).toBeVisible();
  expect(screen.getByRole('img', { name: 'Assurance score unavailable' })).toBeVisible();
  expect(screen.getByText('Coverage 0%')).toBeVisible();
  expect(zero.modules.model_integrity.status).toBe('UNKNOWN');
  expect(zero.modules.model_integrity.finding_count).toBe(0);
  expect(zero.overall.assurance_score).toBeNull();
  rerender(<MemoryRouter><OverviewPage summary={zero} runs={[]} executionAvailable /></MemoryRouter>);
  expect(screen.queryByText('Execution: Authenticated assessment completed')).not.toBeInTheDocument();
  expect(screen.getAllByText('Execution: NOT SUBMITTED')).toHaveLength(4);
  rerender(<MemoryRouter><OverviewPage summary={zero} runs={[{ ...run, assessment_id: 'OTHER' }]} /></MemoryRouter>);
  expect(screen.queryByText('Execution: Authenticated assessment completed')).not.toBeInTheDocument();
  expect(screen.getAllByText('Execution: Submission evidence unavailable')).toHaveLength(4);
  rerender(<MemoryRouter><OverviewPage summary={zero} runs={[{ ...run, authentication: { ...run.authentication, authenticated: false } }]} executionAvailable /></MemoryRouter>);
  expect(screen.queryByText('Execution: Authenticated assessment completed')).not.toBeInTheDocument();
});
