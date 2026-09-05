"""Decode bounded caller-designated D3/D4 evidence into existing module contracts."""
import json
import numpy as np


def load_pair(path):
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict) or set(data) != {'descriptor', 'reference', 'current'}:
        raise ValueError('Comparison JSON requires descriptor, reference and current')
    if any(not isinstance(data[role], list) or not 1 <= len(data[role]) <= 128 for role in ('reference', 'current')):
        raise ValueError('Comparison requires 1–128 samples per window')
    return data


def representation_pair(path):
    from modules.distribution_shift.representation_models import RepresentationSpaceDescriptor, NormalizationMode
    from modules.distribution_shift.representation_profile import RepresentationProfiler
    from modules.distribution_shift.representation_comparison import RepresentationShiftComparator
    data = load_pair(path)
    spec = dict(data['descriptor'])
    spec['normalization'] = NormalizationMode(spec['normalization'])
    descriptor = RepresentationSpaceDescriptor(**spec)
    if descriptor.output_dimension > 64: raise ValueError('At most 64 representation dimensions')
    windows = []
    for role in ('reference', 'current'):
        values = np.asarray(data[role], dtype=np.float64)
        if values.shape != (len(data[role]), descriptor.output_dimension) or not np.isfinite(values).all():
            raise ValueError('Representation data must be a finite numeric matrix matching the descriptor')
        windows.append(values)
    profiler = RepresentationProfiler()
    return RepresentationShiftComparator().compare(profiler.build_reference(windows[0], descriptor), profiler.build_current(windows[1], descriptor))


def prediction_pair(path):
    from modules.distribution_shift.prediction_models import PredictionOutputSpaceDescriptor, PredictionEvidenceTier, PredictionOutputFamily, PredictionRecord
    from modules.distribution_shift.prediction_profile import PredictionProfiler
    from modules.distribution_shift.prediction_comparison import PredictionShiftComparator
    data = load_pair(path)
    spec = dict(data['descriptor'])
    spec['evidence_tier'] = PredictionEvidenceTier(spec['evidence_tier'])
    spec['output_family'] = PredictionOutputFamily(spec['output_family'])
    # Explicit semantics; do not infer normalization or class order.
    if 'probability_semantics' not in spec: raise ValueError('Declare probability semantics')
    spec['class_labels'] = tuple(spec['class_labels'])
    if len(spec['class_labels']) > 64: raise ValueError('At most 64 classes')
    descriptor = PredictionOutputSpaceDescriptor(**spec)
    windows = []
    for role in ('reference', 'current'):
        records = []
        for raw in data[role]:
            item = dict(raw)
            if item.get('probabilities') is not None: item['probabilities'] = tuple(item['probabilities'])
            records.append(PredictionRecord(**item))
        windows.append(records)
    profiler = PredictionProfiler()
    return PredictionShiftComparator().compare(profiler.build_reference(windows[0], descriptor), profiler.build_current(windows[1], descriptor))
