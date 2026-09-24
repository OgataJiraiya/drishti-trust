import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const key = 'drishti-preview-scenario';
const publicPages = ['Overview', 'Findings', 'Distribution Shift', 'Reports'];
const hiddenPages = ['New Assessment', 'Assurance Graph', 'System'];
function LocationProbe() { return <output data-testid="location">{useLocation().pathname}</output>; }
async function renderApp(preview = true, path = '/', scenario = 'critical') {
  vi.resetModules();
  vi.stubEnv('VITE_DRISHTI_DEMO_MODE', 'true');
  vi.stubEnv('VITE_DRISHTI_DEMO_SCENARIO', scenario);
  vi.stubEnv('VITE_DRISHTI_SUBMISSION_PREVIEW', String(preview));
  const { default: App } = await import('../App');
  return render(<MemoryRouter initialEntries={[path]}><App /><LocationProbe /></MemoryRouter>);
}
beforeEach(() => sessionStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); sessionStorage.clear(); });

it('retains every canonical navigation link outside preview', async () => {
  await renderApp(false);
  for (const page of [...publicPages, ...hiddenPages]) expect(within(screen.getByRole('navigation')).getByRole('link', { name: page })).toBeVisible();
});

it('shows the dossier on a fresh root with only four links and demo provenance', async () => {
  await renderApp();
  expect(screen.getByRole('heading', { name: 'Evidence before confidence.' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'High-Risk Pipeline' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Reference Pipeline' })).toBeVisible();
  const snapshot = within(screen.getByRole('complementary', { name: 'Assurance snapshot' }));
  for (const text of ['QUARANTINE', '100%', 'VALID', 'Controlled assessment fixture']) expect(snapshot.getByText(text)).toBeVisible();
  expect(snapshot.getByText(/62.8/)).toBeVisible();
  expect(screen.getByText('PREVIEW / DEMO')).toBeVisible();
  for (const badge of screen.getAllByText('DEMO DATA')) expect(badge).toBeVisible();
  const nav = within(screen.getByRole('navigation'));
  expect(nav.getAllByRole('link').map(link => link.textContent)).toEqual(publicPages);
  for (const page of hiddenPages) expect(nav.queryByRole('link', { name: page })).not.toBeInTheDocument();
  expect(screen.queryByRole('combobox', { name: 'Demo Scenario' })).not.toBeInTheDocument();
  expect(sessionStorage.getItem(key)).toBeNull();
});

const detailPages = [
  ['/findings', 'Findings', 'Findings'],
  ['/distribution', 'Distribution Shift', 'Distribution Shift'],
  ['/reports', 'Reports', 'Assessment assurance summary'],
] as const;
it.each(detailPages)('navigates from fresh landing to %s without redirecting home', async (path, link, heading) => {
  await renderApp();
  await userEvent.click(screen.getByRole('link', { name: link }));
  expect(screen.getByRole('heading', { name: heading })).toBeVisible();
  expect(screen.getByTestId('location')).toHaveTextContent(path);
  expect(screen.getByRole('combobox', { name: 'Demo Scenario' })).toHaveValue('critical');
  if (path === '/findings') expect(screen.getAllByText('FND-2026-0042').length).toBeGreaterThan(0);
});
it.each(detailPages)('supports direct load and fresh refresh of %s', async (path, _link, heading) => {
  const page = await renderApp(true, path);
  expect(screen.getByRole('heading', { name: heading })).toBeVisible();
  expect(screen.getByRole('combobox', { name: 'Demo Scenario' })).toHaveValue('critical');
  expect(screen.getByTestId('location')).toHaveTextContent(path);
  page.unmount();
  await renderApp(true, path);
  expect(screen.getByRole('heading', { name: heading })).toBeVisible();
  expect(screen.getByTestId('location')).toHaveTextContent(path);
});

it.each(detailPages)('restores reference context on direct load and refresh of %s', async (path, _link, heading) => {
  sessionStorage.setItem(key, 'clean');
  const page = await renderApp(true, path);
  expect(screen.getByRole('heading', { name: heading })).toBeVisible();
  expect(screen.getByRole('combobox', { name: 'Demo Scenario' })).toHaveValue('clean');
  if (path === '/reports') {
    expect(screen.getByRole('img', { name: 'Backend assurance score 96.1 out of 100' })).toBeVisible();
    expect(screen.getByText('ACCEPT')).toBeVisible();
  }
  page.unmount();
  await renderApp(true, path);
  expect(screen.getByRole('combobox', { name: 'Demo Scenario' })).toHaveValue('clean');
  expect(screen.getByTestId('location')).toHaveTextContent(path);
});

it.each([
  ['High-Risk Pipeline', 'critical', '62.8', 'QUARANTINE'],
  ['Reference Pipeline', 'clean', '96.1', 'ACCEPT'],
])('opens and persists %s, drives all pages without requests, and resets', async (title, scenario, score, disposition) => {
  const network = vi.fn(() => { throw new Error('Unexpected preview network request'); });
  for (const name of ['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource']) vi.stubGlobal(name, network);
  const print = vi.spyOn(window, 'print').mockImplementation(() => {});
  await renderApp();
  await userEvent.click(screen.getByRole('button', { name: 'Open assessment: ' + title }));
  expect(sessionStorage.getItem(key)).toBe(scenario);
  expect(screen.getByText(score + ' / 100')).toBeVisible();
  expect(screen.getByText('Coverage 100%')).toBeVisible();
  expect(screen.getAllByText(disposition).length).toBeGreaterThan(0);
  const { demoScenarios } = await import('../fixtures/demo');
  const fixture = demoScenarios[scenario];
  expect(screen.getByText(fixture.trusted_finding_count + ' authenticated · ' + fixture.excluded_untrusted_finding_count + ' excluded')).toBeVisible();
  await userEvent.click(screen.getByRole('link', { name: 'Findings' }));
  for (const finding of fixture.latest_findings) expect(screen.getAllByText(finding.finding_id).length).toBeGreaterThan(0);
  await userEvent.click(screen.getByRole('link', { name: 'Distribution Shift' }));
  expect(screen.getByText(fixture.modules.distribution_shift.status)).toBeVisible();
  await userEvent.click(screen.getByRole('link', { name: 'Reports' }));
  expect(screen.getByRole('img', { name: 'Backend assurance score ' + score + ' out of 100' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'PRINT / SAVE AS PDF' }));
  expect(print).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByRole('link', { name: 'Overview' }));
  await userEvent.click(screen.getByRole('button', { name: 'Change assessment' }));
  expect(sessionStorage.getItem(key)).toBeNull();
  expect(screen.getByRole('heading', { name: 'Evidence before confidence.' })).toBeVisible();
  expect(network).not.toHaveBeenCalled();
});

it('changes the compact selector on Reports without changing the route and restores it on refresh', async () => {
  const page = await renderApp(true, '/reports');
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Demo Scenario' }), 'clean');
  expect(sessionStorage.getItem(key)).toBe('clean');
  expect(screen.getByTestId('location')).toHaveTextContent('/reports');
  expect(screen.getByRole('img', { name: 'Backend assurance score 96.1 out of 100' })).toBeVisible();
  page.unmount();
  await renderApp(true, '/reports');
  expect(screen.getByRole('combobox', { name: 'Demo Scenario' })).toHaveValue('clean');
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Demo Scenario' }), 'critical');
  expect(sessionStorage.getItem(key)).toBe('critical');
  expect(screen.getByTestId('location')).toHaveTextContent('/reports');
  expect(screen.getByRole('img', { name: 'Backend assurance score 62.8 out of 100' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Reset demo' }));
  expect(sessionStorage.getItem(key)).toBeNull();
  expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/);
  expect(screen.getByRole('heading', { name: 'Evidence before confidence.' })).toBeVisible();
});

it.each(['unknown', 'null', '../reports'])('ignores unallowlisted session value %s', async value => {
  sessionStorage.setItem(key, value);
  const page = await renderApp();
  expect(screen.getByRole('heading', { name: 'Evidence before confidence.' })).toBeVisible();
  page.unmount();
  await renderApp(true, '/reports');
  expect(screen.getByRole('img', { name: 'Backend assurance score 62.8 out of 100' })).toBeVisible();
});
it('remains usable if browser storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Blocked'); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Blocked'); });
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('Blocked'); });
  await renderApp();
  await userEvent.click(screen.getByRole('button', { name: 'Open assessment: Reference Pipeline' }));
  expect(screen.getByText('96.1 / 100')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Reset demo' }));
  expect(screen.getByRole('heading', { name: 'Evidence before confidence.' })).toBeVisible();
});
it.each(['/new-assessment', '/graph', '/system'])('redirects hidden route %s safely to the fresh landing', async path => {
  await renderApp(true, path);
  expect(screen.getByRole('heading', { name: 'Evidence before confidence.' })).toBeVisible();
  expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/);
});
it('keeps UNKNOWN distinct from safe and preserves an unavailable score', async () => {
  await renderApp(false, '/', 'unknown');
  expect(await screen.findByLabelText('Assurance score unavailable')).toBeVisible();
  await userEvent.click(screen.getByText('Status and decision semantics'));
  expect(screen.getByText(/UNKNOWN does not mean safe/)).toBeVisible();
  expect(screen.queryByText('0.0 / 100')).not.toBeInTheDocument();
});
