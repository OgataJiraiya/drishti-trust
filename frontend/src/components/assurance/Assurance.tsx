import type { AssuranceSummary } from '../../api/types';
import { Card, toneFor } from '../ui';

export const fmtScore = (value: number | null) => value === null ? 'Unavailable' : value.toFixed(1);
export const fmtPct = (value: number) => `${Math.round(value * 100)}%`;

export function ScoreGauge({ score }: { score: number | null }) { return <div className="score-readout" role="img" aria-label={score === null ? 'Assurance score unavailable' : `Backend assurance score ${score.toFixed(1)} out of 100`}><strong>{score === null ? '—' : score.toFixed(1)}</strong><span>/ 100</span></div>; }

export function AssuranceHero({ summary }: { summary: AssuranceSummary }) {
  const overall = summary.overall;
  return <div className="reference-status-row">
    <Card className="reference-score-panel"><span className="section-label">ASSURANCE SCORE</span><ScoreGauge score={overall.assurance_score} /><span className={`plain-status tone-${toneFor(overall.score_status)}`}>{overall.score_status}</span></Card>
    <Card className={`reference-disposition-panel status-${overall.disposition.toLowerCase()}`}><span className="section-label">SYSTEM DISPOSITION</span><strong>{overall.disposition}</strong><p>{overall.reason}</p>{overall.critical_overrides.length > 0 && <small>Critical override: <code>{overall.critical_overrides.join(' · ')}</code></small>}</Card>
  </div>;
}
