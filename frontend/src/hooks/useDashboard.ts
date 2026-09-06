import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { fetchAssessments, fetchCurrent, fetchHealth } from '../api/assessments';
import { fetchSummary } from '../api/summary';
import type { Assessment, AssuranceSummary, BackendState } from '../api/types';
import { demoAssessments, getDemoSummary } from '../fixtures/demo';

const DEMO = import.meta.env.VITE_DRISHTI_DEMO_MODE === 'true';
export function useDashboard() {
  const [summary, setSummary] = useState<AssuranceSummary>();
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [selected, setSelected] = useState('');
  const [backend, setBackend] = useState<BackendState>('OFFLINE');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [noActive, setNoActive] = useState(false);
  const generation = useRef(0);
  const load = useCallback(async (id?: string) => {
    const request = ++generation.current;
    await Promise.resolve();
    if (request !== generation.current) return;
    setLoading(true); setError(undefined);
    try {
      if (DEMO) {
        const chosen = id ?? demoAssessments[0].assessment_id;
        setAssessments(demoAssessments); setSelected(chosen);
        setSummary(getDemoSummary(chosen)); setBackend('ONLINE'); setNoActive(false);
        return;
      }
      const health = await fetchHealth();
      const list = await fetchAssessments();
      let target = id;
      let missingActive = false;
      if (!target) {
        try { target = (await fetchCurrent()).assessment_id; }
        catch (e) {
          if (e instanceof ApiError && e.status === 404) missingActive = true;
          else throw e;
        }
      }
      const result = await fetchSummary(target);
      if (request !== generation.current) return;
      setBackend(health.status === 'ok' ? 'ONLINE' : 'DEGRADED');
      setAssessments(list.items); setSelected(target ?? '');
      setNoActive(missingActive); setSummary(result);
    } catch (e) {
      if (request !== generation.current) return;
      setBackend('OFFLINE'); setError(e instanceof Error ? e.message : 'Unable to load dashboard.');
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }, []);
  const invalidate = useCallback(() => { generation.current++; }, []);
  useEffect(() => { void load(); return invalidate; }, [load, invalidate]);
  const select = (id: string) => { setSelected(id); void load(id || undefined); };
  return { summary, assessments, selected, backend, loading, error, noActive, demo: DEMO,
    retry: () => load(selected || undefined), select };
}
