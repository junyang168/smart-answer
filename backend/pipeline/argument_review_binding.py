"""Bind local review reports to their complete candidate; no model contract changes."""
import hashlib


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def input_binding(candidate_raw, rows):
    ids = sorted(r['job_id'] for r in rows)
    if len(ids) != len(set(ids)):
        raise ValueError('review input binding has duplicate record IDs')
    return {'candidate_sha256': sha(candidate_raw), 'record_ids': ids}


def bind_report(report, candidate_raw, rows):
    return report | {'input_binding': input_binding(candidate_raw, rows)}


def require_binding(report, candidate_raw, rows, *, label):
    expected = input_binding(candidate_raw, rows)
    count = report.get('claims' if label == 'story' else 'records')
    if report.get('input_binding') != expected or count != len(rows):
        raise ValueError(f'{label} input binding differs: report must match the original candidate and all its records')


def require_unchanged(path, candidate_raw):
    if path.read_bytes() != candidate_raw:
        raise ValueError('review candidate changed while checking; no report written')
