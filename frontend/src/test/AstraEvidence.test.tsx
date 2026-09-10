import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { demoScenarios } from '../fixtures/demo';
import { useWorkspaceData } from '../hooks/useWorkspaceData';
import { FindingsPage } from '../pages/FindingsPage';
import { ReportsPage } from '../pages/ReportsPage';

const summary = { ...demoScenarios.critical, assessment_id: 'A', assessment_status: 'ACTIVE' as const };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each([12, 105])('loads all %i scoped findings into explorer and report', async (count) => {
  const findings = Array.from({ length: count }, (_, i) => ({ ...summary.latest_findings[0], finding_id: `A-${i}` }));
  const scoped = { ...summary, latest_findings: findings.slice(0, 10) };
  const fetch = vi.fn(async (input: string) => {
    const url = new URL(input, 'http://local');
    if (url.pathname.endsWith('/runs')) return { ok: true, json: async () => ({ total: 0, items: [] }) };
    expect(url.pathname).toBe('/api/assessments/A/findings');
    const page = Number(url.searchParams.get('page'));
    return { ok: true, json: async () => ({ total: count, page, page_size: 100, items: findings.slice((page - 1) * 100, page * 100) }) };
  });
  vi.stubGlobal('fetch', fetch);
  function Workspace() {
    const data = useWorkspaceData(scoped);
    return <MemoryRouter><FindingsPage findings={data.findings} limited={data.detailUnavailable} /><ReportsPage summary={scoped} findings={data.findings} /></MemoryRouter>;
  }
  render(<Workspace />);
  await waitFor(() => expect(document.querySelectorAll('.explorer-row')).toHaveLength(count));
  expect(document.querySelectorAll('.report-finding')).toHaveLength(count);
  expect(screen.queryByText(/Showing the latest authenticated summary subset/)).not.toBeInTheDocument();
  expect(fetch.mock.calls.filter(([url]) => url.includes('/findings'))).toHaveLength(Math.ceil(count / 100));
});

it('does not publish an old assessment response after a selection change', async () => {
  let resolveOld: (value: unknown) => void = () => {};
  const oldResponse = new Promise(resolve => { resolveOld = resolve; });
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    if (input.includes('/runs')) return { ok: true, json: async () => ({ total: 0, items: [] }) };
    if (input.includes('/A/')) return { ok: true, json: () => oldResponse };
    return { ok: true, json: async () => ({ total: 0, page: 1, page_size: 100, items: [] }) };
  }));
  const { result, rerender } = renderHook(({ value }) => useWorkspaceData(value), { initialProps: { value: summary } });
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/A/findings'), expect.anything()));
  rerender({ value: { ...summary, assessment_id: 'B', latest_findings: [] } });
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => resolveOld({ total: 1, items: [summary.latest_findings[0]] }));
  expect(result.current.findings).toEqual([]);
  expect(result.current.detailUnavailable).toBe(false);
});

it('retains an explicit incomplete state when a later findings page fails', async () => {
  const findings = Array.from({ length: 100 }, (_, i) => ({ ...summary.latest_findings[0], finding_id: `A-${i}` }));
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    if (input.includes('/runs')) return { ok: true, json: async () => ({ total: 0, items: [] }) };
    if (input.includes('page=2&')) return { ok: false, status: 500 };
    return { ok: true, json: async () => ({ total: 105, page: 1, page_size: 100, items: findings }) };
  }));
  const { result } = renderHook(() => useWorkspaceData(summary));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.detailUnavailable).toBe(true);
  expect(result.current.findings).toEqual(summary.latest_findings);
  render(<ReportsPage summary={summary} findings={result.current.findings} limited={result.current.detailUnavailable} />);
  expect(screen.getByRole('status')).toHaveTextContent('incomplete');
});
