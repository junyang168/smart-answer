"""One explicitly authorized extra correction/review in the original Route job."""
import json
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.cvp_sql_commit import binding, check_binding
from backend.pipeline.viewpoint_resolution_runtime import write_immutable

VERSION = 'wang_cvp_route_round_extension_v1'
FILENAME = 'correction-round-extension.json'


def round_extension(root, job, policy, authorization=None):
    """Keep old policy, consumed rounds and provider answers immutable."""
    root = Path(root).resolve()
    path = root / FILENAME
    if path.exists():
        receipt = json.loads(path.read_text())
        body = {k:v for k,v in receipt.items() if k != 'artifact_sha256'}
        if (receipt.get('artifact_sha256') != sha256_json(body)
                or receipt.get('job') != job
                or receipt.get('original_max_correction_rounds') != policy['max_correction_rounds']
                or receipt.get('effective_max_correction_rounds') != policy['max_correction_rounds'] + 1):
            raise ValueError('Route round extension identity or original policy changed')
        check_binding(receipt['authorization'])
        if authorization is not None and binding(authorization) != receipt['authorization']:
            raise ValueError('Route round extension authorization changed')
        for name, pin in receipt['original_files'].items():
            if Path(name).name != name or binding(root/name) != pin:
                raise ValueError('Route round extension original artifact changed')
        return receipt
    if authorization is None:
        return None
    if root.name != job['job_id'] or (root/'route-cvp-run.json').exists() or (root/'route-change-plan.json').exists():
        raise ValueError('Route round extension requires the original uncommitted job')
    read = lambda name:json.loads((root/name).read_text())
    material = read('cvp-route-materials.json')
    if (material.get('artifact_sha256') != job['material_sha256']
            or material['viewpoint_id'] != job['viewpoint_id']):
        raise ValueError('Route round extension material differs from original job')
    reviews = sorted(root.glob('route-review-[0-9][0-9][0-9].json'))
    if not reviews:
        raise ValueError('Route round extension requires a saved independent review')
    latest = reviews[-1]
    ordinal = int(latest.stem.rsplit('-',1)[1])
    review = read(latest.name)
    validation = review['validation']
    consumed = ordinal + len(list(root.glob('route-proposal-validation-[0-9][0-9][0-9].json')))
    if (consumed != policy['max_correction_rounds']
            or validation['outcome'] != 'findings'
            or not validation['reconsideration_required'] or validation['exception_keys']):
        raise ValueError('Route round extension requires exhausted ordinary corrections and correctable findings')
    for suffix in ('.call-intent.json', '.request-encoding.json'):
        for intent in root.glob('*'+suffix):
            if not (root/(intent.name.removesuffix(suffix)+'.json')).exists():
                raise ValueError('Route round extension cannot repeat an unknown call')
    auth_pin = binding(authorization)
    direction = json.loads(Path(authorization).read_text())
    expected = {'schema_version':VERSION, 'authorized_by':'owner',
        'owner_direction':direction.get('owner_direction'),
        'job_id':job['job_id'], 'viewpoint_id':job['viewpoint_id'],
        'material_sha256':job['material_sha256'], 'policy_sha256':job['policy_sha256'],
        'original_max_correction_rounds':policy['max_correction_rounds'],
        'additional_correction_rounds':1, 'latest_review_round':ordinal,
        'latest_review_sha256':review['review_sha256']}
    if direction != expected or not isinstance(direction.get('owner_direction'),str) or not direction['owner_direction'].strip():
        raise ValueError('Route round extension authorization differs from exact job/review/one round')
    receipt = {'schema_version':VERSION, 'job':job, 'authorization':auth_pin,
        'original_max_correction_rounds':policy['max_correction_rounds'],
        'effective_max_correction_rounds':policy['max_correction_rounds']+1,
        'previous_corrections_consumed':consumed, 'latest_review_round':ordinal,
        'latest_review_sha256':review['review_sha256'], 'human_content_approval':False,
        'original_files':{p.name:binding(p) for p in sorted(root.glob('*.json'))}}
    receipt['artifact_sha256'] = sha256_json(receipt)
    write_immutable(path, receipt, require_new=True)
    return receipt
