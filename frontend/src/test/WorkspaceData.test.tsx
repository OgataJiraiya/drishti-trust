import { renderHook, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { demoScenarios } from '../fixtures/demo';
import { useWorkspaceData } from '../hooks/useWorkspaceData';

const scoped = vi.hoisted(() => Array.from({ length: 12 }, (_, index) => ({
  ...({
    finding_id: `F-SCOPED-${index}`,
    module: 'dataset_integrity' as const,
    asset_type: 'sample',
    asset_id: `sample:${index}`,
    category: 'TEST',
    severity: 'LOW' as const,
    confidence: 0.5,
    reason: 'Scoped assessment finding',
    evidence: ['source=test'],
    recommendation: 'REVIEW' as const,
    limitations: [],
  }),
})));

vi.mock('../api/workspaces', () => ({
  fetchRuns: async () => ({ total: 0, limit: 100, offset: 0, items: [] }),
  fetchAssessmentFindings: async () => ({ total: scoped.length, page: 1, page_size: 100, items: scoped }),
  fetchSnapshot: async () => { throw new Error('not requested for active assessment'); },
  fetchSnapshotVerification: async () => { throw new Error('not requested for active assessment'); },
}));

it('loads the assessment-scoped finding set instead of the ten-item summary preview', async () => {
  const summary = { ...demoScenarios.critical, assessment_status: 'ACTIVE' as const };
  const { result } = renderHook(() => useWorkspaceData(summary, false));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.findings).toHaveLength(12);
  expect(result.current.findings).toEqual(scoped);
  expect(result.current.detailUnavailable).toBe(false);
});
