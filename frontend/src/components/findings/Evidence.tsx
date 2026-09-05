import { useState } from 'react';

const layers: Record<string, string> = { IMAGE_STATISTICAL: 'Image / statistical', REPRESENTATION: 'Representation', PREDICTION_OUTPUT: 'Prediction output' };
const labels: Record<string, string> = { pattern_code: 'Interpretation', interpretation_status: 'Interpretation coverage', reference_artifact_id: 'Reference', candidate_artifact_id: 'Candidate', reference_sha256: 'Reference SHA-256', current_sha256: 'Current SHA-256' };
export function evidenceConcept(raw: string): { label: string; value: string } | null {
  const index = raw.indexOf('=');
  if (index < 0) return null;
  const key = raw.slice(0, index), value = raw.slice(index + 1);
  if (key === 'changed_layers' && value.split(',').every(item => layers[item] || item === 'none')) return { label: 'Changed layers', value: value.split(',').map(item => layers[item] ?? 'None observed').join(' · ') };
  const source = /^source_comparison_id\[(IMAGE_STATISTICAL|REPRESENTATION|PREDICTION_OUTPUT)\]$/.exec(key);
  if (source) return { label: `Source comparison · ${layers[source[1]]}`, value };
  if (labels[key]) return { label: labels[key], value: key === 'pattern_code' ? value.replaceAll('_', ' ').toLowerCase() : value };
  return null;
}
function EvidenceValue({ value }: { value: string }) {
  const [state, setState] = useState('Copy');
  return <div className="evidence-value"><code title={value}>{value}</code><button aria-label={`Copy ${value}`} onClick={() => { void navigator.clipboard.writeText(value).then(() => setState('Copied'), () => setState('Copy unavailable')); }}>{state}</button></div>;
}
export function EvidenceSummary({ evidence }: { evidence: string[] }) {
  return <section className="evidence-summary"><h2>EVIDENCE SUMMARY</h2>{evidence.map((raw, index) => { const concept = evidenceConcept(raw); return concept ? <div key={index}><h3>{concept.label}</h3><EvidenceValue value={concept.value} /></div> : /^(?:(?:mapping_policy|bundle_id|interpretation_id)=.+$|layer=(?:IMAGE_STATISTICAL|REPRESENTATION|PREDICTION_OUTPUT);coverage=.+;observation=.+$|shifted_features\[(?:IMAGE_STATISTICAL|REPRESENTATION|PREDICTION_OUTPUT)\]=.+$)/.test(raw) ? null : <EvidenceValue key={index} value={raw} />; })}</section>;
}
export function Evidence({ evidence }: { evidence: string[] }) {
  return <><EvidenceSummary evidence={evidence} /><details><summary>TECHNICAL EVIDENCE · Show technical evidence</summary>{evidence.map((raw, index) => <EvidenceValue key={index} value={raw} />)}</details></>;
}
