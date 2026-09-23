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
  expect(screen.getByText('DEMO DATA')).toBeVisible();
  expect(screen.getByText('SIH Submission Preview · Core Integrity Assurance Workflow')).toBeVisible();
  expect(screen.getByText('PREVIEW / DEMO')).toBeVisible();
  expect(screen.queryByText(/Local backend online/i)).not.toBeInTheDocument();
});

it('uses the critical fixture and makes no request across all preview pages', async () => {
  const fetchSpy = vi.fn(() => { throw new Error('Preview attempted a network request'); });
  vi.stubGlobal('fetch', fetchSpy);
  const view = await renderApp(true);
  expect(screen.getByRole('img', { name: 'Backend assurance score 62.8 out of 100' })).toBeVisible();
  expect(screen.getByText('Coverage 100%')).toBeVisible();
  expect(screen.getAllByText('QUARANTINE').length).toBeGreaterThan(0);
  expect(screen.getByText(/18 authenticated · 3 excluded/)).toBeVisible();
  expect(screen.getByText(/Audit VALID · 184 records checked/)).toBeVisible();
  expect(fetchSpy).not.toHaveBeenCalled();
  view.unmount();

  for (const [path, heading] of [['/findings', 'Findings'], ['/distribution', 'Distribution Shift'], ['/reports', 'Assessment assurance summary']]) {
    const page = await renderApp(true, path);
    expect(screen.getByRole('heading', { name: heading })).toBeVisible();
    expect(fetchSpy).not.toHaveBeenCalled();
    page.unmount();
  }
});

it('redirects hidden preview routes to Overview', async () => {
  await renderApp(true, '/new-assessment');
  expect(screen.getByRole('img', { name: 'Backend assurance score 62.8 out of 100' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'New Assessment' })).not.toBeInTheDocument();
});

it('keeps UNKNOWN distinct from safe and preserves an unavailable score', async () => {
  await renderApp(true, '/', 'unknown');
  expect(screen.getByLabelText('Assurance score unavailable')).toBeVisible();
  expect(screen.getAllByText('UNKNOWN').length).toBeGreaterThan(0);
  await userEvent.click(screen.getByText('Status and decision semantics'));
  expect(screen.getByText(/UNKNOWN does not mean safe/)).toBeVisible();
  expect(screen.queryByText(/^0\.0 \/ 100$/)).not.toBeInTheDocument();
});
