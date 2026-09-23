import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { demoScenarios } from '../fixtures/demo';
vi.mock('../api/assessments', () => ({
  fetchHealth: async () => ({ status: 'ok' }),
  fetchAssessments: async () => ({ items: [] }),
  fetchCurrent: async () => ({ assessment_id: 'INITIAL' }),
}));
const pending = vi.hoisted(() => new Map<string, (value: unknown) => void>());
vi.mock('../api/summary', () => ({ fetchSummary: (id: string) => new Promise(resolve => pending.set(id, resolve)) }));
afterEach(() => vi.unstubAllEnvs());
it('keeps the latest selection when an older summary arrives last', async () => {
  vi.stubEnv('VITE_DRISHTI_DEMO_MODE', 'false');
  const { useDashboard } = await import('../hooks/useDashboard');
  const { result } = renderHook(() => useDashboard());
  await waitFor(() => expect(pending.has('INITIAL')).toBe(true));
  act(() => result.current.select('NEW'));
  await waitFor(() => expect(pending.has('NEW')).toBe(true));
  await act(async () => pending.get('NEW')!({ ...demoScenarios.unknown, assessment_id: 'NEW' }));
  await act(async () => pending.get('INITIAL')!({ ...demoScenarios.critical, assessment_id: 'INITIAL' }));
  expect(result.current.selected).toBe('NEW');
  expect(result.current.summary?.assessment_id).toBe('NEW');
  expect(result.current.loading).toBe(false);
});
