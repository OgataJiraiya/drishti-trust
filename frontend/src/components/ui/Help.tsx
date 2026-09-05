import type { AssuranceSummary, ModuleKey } from '../../api/types';

const descriptions: Record<ModuleKey, string> = {
  dataset_integrity: 'Checks dataset samples, duplication, labels, metadata, source/contributor risk and related anomalies where evidence is supplied.',
  model_integrity: 'Checks model artifact identity, structure, parameters and bounded behavioral integrity evidence.',
  inference_integrity: 'Checks signed inference receipts, output integrity, replay and execution/provenance evidence.',
  distribution_shift: 'Compares reference and current observable image/statistical, representation and prediction-output behavior.',
};
export function ModuleHelp({ module }: { module: ModuleKey }) {
  return <details className="module-help"><summary aria-label={`About ${module.replaceAll('_', ' ')}`}>About this module</summary><p>{descriptions[module]}</p></details>;
}
export function StatusHelp() {
  return <details className="status-help"><summary>Status and decision semantics</summary><dl>
    <dt>REVIEW</dt><dd>Analyst investigation required.</dd>
    <dt>HIGH RISK</dt><dd>Significant evidence affected this module's backend risk state. This is a module status, not a Finding recommendation.</dd>
    <dt>QUARANTINE</dt><dd>Backend system disposition: do not release/deploy pending investigation.</dd>
    <dt>REJECT</dt><dd>A Finding recommendation may prohibit acceptance of the affected artifact/evidence. It does not itself reject the entire pipeline.</dd>
  </dl><p>Finding recommendations and backend system dispositions are distinct. UNKNOWN does not mean safe. No findings and complete coverage do not establish safety.</p></details>;
}
export function HistoricalContext({ summary, selected }: { summary: AssuranceSummary; selected: string }) {
  return selected && summary.assessment_id === selected && summary.assessment_status === 'SEALED'
    ? <div className="no-active" role="status"><strong>HISTORICAL ASSESSMENT</strong><p>{summary.assessment_id} · {summary.assessment_status}</p>Viewing authenticated historical assessment evidence.</div> : null;
}
