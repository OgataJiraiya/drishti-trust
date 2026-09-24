import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';

const publicPages = ['Overview', 'Findings', 'Distribution Shift', 'Reports'];
const hiddenPages = ['New Assessment', 'Assurance Graph', 'System'];

async function renderApp(preview: boolean, path = '/', scenario = 'critical') {
  vi.resetModules();
  vi.stubEnv('VITE_DRISHTI_DEMO_MODE', 'true');
  vi.stubEnv('VITE_DRISHTI_DEMO_SCENARIO', scenario);
  vi.stubEnv('VITE_DRISHTI_SUBMISSION_PREVIEW', String(preview));
  const { default: App } = await import('../App');
  return render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
}

afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

it('retains every canonical navigation link when preview flag is absent', async () => {
  await renderApp(false);
  const navigation = screen.getByRole('navigation');
  for (const page of [...publicPages, ...hiddenPages]) {
    expect(within(navigation).getByRole('link', { name: page })).toBeVisible();
  }
});

it('exposes exactly four public links, demo provenance, and truthful preview status', async () => {
  await renderApp(true);
  const navigation = screen.getByRole('navigation');
  expect(within(navigation).getAllByRole('link').map((link) => link.textContent)).toEqual(publicPages);
  for (const page of hiddenPages) expect(within(navigation).queryByRole('link', { name: page })).not.toBeInTheDocument();
  for (const badge of screen.getAllByText('DEMO DATA')) expect(badge).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Verify trust before deployment' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Compromised AI Pipeline' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Clean Reference Pipeline' })).toBeVisible();
  expect(screen.queryByRole('combobox', { name: 'Demo Scenario' })).not.toBeInTheDocument();
  expect(screen.getByText('SIH Submission Preview · Core Integrity Assurance Workflow')).toBeVisible();
  expect(screen.getByText('PREVIEW / DEMO')).toBeVisible();
  expect(screen.queryByText(/Local backend online/i)).not.toBeInTheDocument();
});

it.each([
  ['critical', 'Review High-Risk Assessment', '62.8', 'QUARANTINE'],
  ['clean', 'Review Clean Assessment', '96.1', 'ACCEPT'],
])('uses the %s fixture across all four pages without network requests', async (scenario, button, score, disposition) => {
  const network = vi.fn(() => { throw new Error('Preview attempted a network request'); });
  for (const name of ['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource']) vi.stubGlobal(name, network);
  const print = vi.spyOn(window, 'print').mockImplementation(() => {});
  await renderApp(true);
  await userEvent.click(screen.getByRole('button', { name: button }));
  const { demoScenarios } = await import('../fixtures/demo');
  const fixture = demoScenarios[scenario];
  expect(screen.getByRole('img', { name: 'Backend assurance score ' + score + ' out of 100' })).toBeVisible();
  expect(screen.getByText(score + ' / 100')).toBeVisible();
  expect(screen.getByText('Coverage 100%')).toBeVisible();
  expect(screen.getAllByText(disposition).length).toBeGreaterThan(0);
  expect(screen.getByText(fixture.trusted_finding_count + ' authenticated · ' + fixture.excluded_untrusted_finding_count + ' excluded')).toBeVisible();
  expect(screen.getByText('Audit VALID · ' + fixture.audit_integrity.records_checked + ' records checked')).toBeVisible();
  expect(screen.getByRole('combobox', { name: 'Demo Scenario' })).toHaveValue(scenario);
  await userEvent.click(screen.getByRole('link', { name: 'Findings' }));
  expect(screen.getByRole('heading', { name: 'Findings' })).toBeVisible();
  for (const finding of fixture.latest_findings) expect(screen.getAllByText(finding.finding_id).length).toBeGreaterThan(0);
  await userEvent.click(screen.getByRole('link', { name: 'Distribution Shift' }));
  expect(screen.getByRole('heading', { name: 'Distribution Shift' })).toBeVisible();
  expect(screen.getByText(fixture.modules.distribution_shift.status)).toBeVisible();
  await userEvent.click(screen.getByRole('link', { name: 'Reports' }));
  expect(screen.getByRole('heading', { name: 'Assessment assurance summary' })).toBeVisible();
  expect(screen.getByRole('img', { name: 'Backend assurance score ' + score + ' out of 100' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'PRINT / SAVE AS PDF' }));
  expect(print).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByRole('link', { name: 'Overview' }));
  expect(screen.getByText(score + ' / 100')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Change demo scenario' }));
  expect(screen.getByRole('heading', { name: 'Verify trust before deployment' })).toBeVisible();
  expect(screen.queryByText(score + ' / 100')).not.toBeInTheDocument();
  expect(network).not.toHaveBeenCalled();
  print.mockRestore();
});

it('keeps the compact selector and guided selection in sync', async () => {
  await renderApp(true);
  await userEvent.click(screen.getByRole('button', { name: 'Review High-Risk Assessment' }));
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Demo Scenario' }), 'clean');
  expect(screen.getByText('96.1 / 100')).toBeVisible();
  expect(screen.getByText('Reference demo loaded. Explore assurance details and reporting.')).toBeVisible();
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Demo Scenario' }), 'critical');
  expect(screen.getByText('62.8 / 100')).toBeVisible();
  expect(screen.getByText('High-risk demo loaded. Explore Findings, Distribution Shift and Reports.')).toBeVisible();
});

it('redirects hidden preview routes to the landing', async () => {
  await renderApp(true, '/new-assessment');
  expect(screen.getByRole('heading', { name: 'Verify trust before deployment' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'New Assessment' })).not.toBeInTheDocument();
});

it('keeps UNKNOWN distinct from safe and preserves an unavailable score', async () => {
  // UNKNOWN remains part of the normal prototype; public choices are critical/clean only.
  await renderApp(false, '/', 'unknown');
  expect(await screen.findByLabelText('Assurance score unavailable')).toBeVisible();
  expect(screen.getAllByText('UNKNOWN').length).toBeGreaterThan(0);
  await userEvent.click(screen.getByText('Status and decision semantics'));
  expect(screen.getByText(/UNKNOWN does not mean safe/)).toBeVisible();
  expect(screen.queryByText(/^0\.0 \/ 100$/)).not.toBeInTheDocument();
});
