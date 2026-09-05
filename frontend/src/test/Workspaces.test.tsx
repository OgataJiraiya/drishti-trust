import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { demoAssessments, demoScenarios } from '../fixtures/demo';
import { DistributionPage } from '../pages/DistributionPage';
import { FindingsPage } from '../pages/FindingsPage';
import { GraphPage } from '../pages/GraphPage';
import { ReportsPage } from '../pages/ReportsPage';
import { SystemPage } from '../pages/SystemPage';
const summary = demoScenarios.critical, findings = summary.latest_findings;
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('findings explorer', () => {
  it('renders reference master-detail view and Frozen Finding fields', () => { render(<MemoryRouter><FindingsPage findings={findings} /></MemoryRouter>); expect(screen.getByRole('heading', { name: 'Findings' })).toBeVisible(); expect(screen.getByLabelText('Finding details')).toHaveTextContent('REPLAY DETECTED'); expect(screen.getByText('WHY WAS THIS FLAGGED?')).toBeVisible(); expect(screen.getByText('EVIDENCE SUMMARY')).toBeVisible(); });
  it('filters severity, module, recommendation and search through URL state', async () => { render(<MemoryRouter><FindingsPage findings={findings} /></MemoryRouter>); await userEvent.selectOptions(screen.getByLabelText('Severity'), 'HIGH'); expect(document.querySelector('.explorer-list')).toHaveTextContent('MODEL SUBSTITUTION'); expect(document.querySelector('.explorer-list')).not.toHaveTextContent('REPLAY DETECTED'); await userEvent.selectOptions(screen.getByLabelText('Module'), 'model_integrity'); await userEvent.selectOptions(screen.getByLabelText('Recommendation'), 'QUARANTINE'); await userEvent.type(screen.getByLabelText('Category or asset search'), 'substitution'); expect(screen.getAllByRole('button', { name: /FND-2026-0039/ })).toHaveLength(1); });
  it('hydrates URL filters', () => { render(<MemoryRouter initialEntries={['/findings?severity=MEDIUM&module=distribution_shift']}><FindingsPage findings={findings} /></MemoryRouter>); expect(screen.getByLabelText('Severity')).toHaveValue('MEDIUM'); expect(document.querySelector('.explorer-list')).toHaveTextContent('FEATURE DRIFT'); });
  it('copies IDs and renders read-only actions', async () => { const writeText = vi.fn().mockResolvedValue(undefined); Object.assign(navigator, { clipboard: { writeText } }); render(<MemoryRouter><FindingsPage findings={findings} /></MemoryRouter>); await userEvent.click(screen.getByLabelText('Copy finding id')); await waitFor(() => expect(writeText).toHaveBeenCalledWith('FND-2026-0042')); expect(screen.getByText('Copied')).toBeVisible(); await userEvent.click(screen.getByLabelText('Copy asset id')); expect(screen.getByRole('button', { name: 'ACCEPT' })).toBeDisabled(); });
});

describe('distribution shift', () => {
  it('renders honest live unavailability and no global drift score', () => { render(<MemoryRouter><DistributionPage summary={summary} findings={findings} demo={false} /></MemoryRouter>); expect(screen.getByText(/Detailed detector visualization is not stored/i)).toBeVisible(); expect(screen.queryByText(/drift score/i)).not.toBeInTheDocument(); });
  it('renders demo comparison and interpretation boundary', async () => { render(<DistributionPage summary={summary} findings={findings} demo />); expect(screen.getByRole('img', { name: /brightness distributions/ })).toBeVisible(); await userEvent.click(screen.getByText('LIMITATIONS')); expect(screen.getByText(/does not establish common cause/)).toBeVisible(); expect(screen.getByText(/D5 Multi-signal/)).toBeVisible(); });
});

describe('assurance graph', () => {
  it('renders and changes demo node inspector', async () => { render(<GraphPage summary={summary} findings={findings} runs={[]} demo />); expect(screen.getByText('Evidence relationships for this assessment')).toBeVisible(); await userEvent.click(screen.getByRole('button', { name: /Vision Model v4/ })); expect(screen.getByText('NODE DETAILS').parentElement).toHaveTextContent('MODEL-DEMO-04'); });
  it('does not fabricate a live relationship chain', () => { render(<GraphPage summary={summary} findings={findings} runs={[]} demo={false} />); expect(screen.getByText('No complete provenance graph available')).toBeVisible(); expect(screen.queryByText('Demo contributor identity')).not.toBeInTheDocument(); });
});

describe('report and system', () => {
  it('renders backend report values and prints locally', async () => { const print = vi.spyOn(window, 'print').mockImplementation(() => {}); render(<ReportsPage summary={summary} findings={findings} />); expect(screen.getByText('62.8')).toBeVisible(); expect(screen.getAllByText('QUARANTINE').length).toBeGreaterThan(0); expect(screen.getByText('LIMITATIONS')).toBeVisible(); await userEvent.click(screen.getByText('PRINT / SAVE AS PDF')); expect(print).toHaveBeenCalled(); });
  it.each(['DRAFT', 'ACTIVE', 'SEALED'] as const)('renders exact %s lifecycle state', (status) => { render(<SystemPage summary={{ ...summary, assessment_status: status }} current={{ ...demoAssessments[0], status }} backend="ONLINE" runs={[]} />); expect(screen.getByText('CURRENT ASSESSMENT STATE').parentElement).toHaveTextContent(status); expect(screen.getAllByText('NOT SUBMITTED')).toHaveLength(4); });
  it('shows authenticated run and sealed snapshot verification', () => { const run = { run_id: 'RUN-1', assessment_id: 'A', module: 'dataset_integrity' as const, producer: 'P', producer_version: null, request_hash: 'a'.repeat(64), total_findings: 2, created_findings: 2, existing_findings: 0, finding_ids: [], created_at: '2026-01-01', authentication: { authenticated: true, mode: 'ED25519' as const, producer_id: 'P', key_id: 'K', key_fingerprint: null, request_hash: 'a'.repeat(64), authenticated_at: '2026-01-01' } }; render(<SystemPage summary={{ ...summary, assessment_status: 'SEALED' }} backend="ONLINE" runs={[run]} snapshot={{ assessment_id: 'A', schema_version: '1', payload: {}, summary_hash: 'a'.repeat(64), run_set_hash: 'b'.repeat(64), run_count: 1, trusted_finding_count: 2, excluded_untrusted_finding_count: 0, created_at: '2026-01-01' }} verification={{ assessment_id: 'A', status: 'VALID', stored_summary_hash: 'a', recomputed_summary_hash: 'a', stored_run_set_hash: 'b', recomputed_run_set_hash: 'b' }} />); expect(screen.getByText('RUN-1')).toBeVisible(); expect(screen.getByText('SNAPSHOT AVAILABLE')).toBeVisible(); expect(screen.getAllByText('VALID').length).toBeGreaterThan(0); });
});
