import css from '../styles/globals.css?raw';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { demoScenarios } from '../fixtures/demo';
import { OverviewPage } from '../pages/OverviewPage';
import { SystemPage } from '../pages/SystemPage';

let stylesheet: HTMLStyleElement;
beforeEach(() => {
  stylesheet = document.createElement('style');
  stylesheet.textContent = css;
  document.head.append(stylesheet);
});
afterEach(() => { cleanup(); stylesheet.remove(); });

it('keeps backend coverage and score status in the visible overview flow', () => {
  render(<MemoryRouter><OverviewPage summary={demoScenarios.critical} /></MemoryRouter>);
  const context = screen.getByLabelText('Evidence context');
  expect(context).toHaveTextContent('Coverage 100%');
  expect(context).toHaveTextContent('COMPLETE');
  expect(context).toBeVisible();
  // jsdom has no layout: check computed positioning as well as visibility so
  // offscreen accessibility-only text cannot satisfy judge-visible evidence.
  expect(getComputedStyle(context).position).toBe('static');
});

it('retains final mobile overrides after desktop workstation rules', () => {
  expect(css).toMatch(/@media screen and \(max-width: 600px\)[\s\S]*\.distribution-page, \.report-page[\s\S]*grid-template-columns: minmax\(0, 1fr\)/);
});

it('shows authentication and run identity with the actual workstation stylesheet', () => {
  render(<SystemPage summary={demoScenarios.critical} backend="ONLINE" runs={[{
    run_id: 'FULL-CONCERN-TEST-RUN-1', assessment_id: demoScenarios.critical.assessment_id,
    module: 'dataset_integrity', producer: 'full-system-dataset_integrity',
    producer_version: '1', request_hash: 'a'.repeat(64), total_findings: 1,
    created_findings: 1, existing_findings: 0, finding_ids: [], created_at: '2026-09-05',
    authentication: { authenticated: true, mode: 'ED25519', producer_id: 'P',
      key_id: 'K', key_fingerprint: null, request_hash: 'a'.repeat(64), authenticated_at: '2026-09-05' },
  }]} />);
  expect(screen.getByText('FULL-CONCERN-TEST-RUN-1')).toBeVisible();
  expect(screen.getByText(/1 findings · AUTHENTICATED/)).toBeVisible();
});
