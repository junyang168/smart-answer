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
    additions = sorted(root.glob('correction-round-extension-[0-9][0-9][0-9].json'))
    if additions and not path.exists():
        raise ValueError('Route round extension lost its original reservation')
    paths = ([path] if path.exists() else []) + additions
    previous = None
    for ordinal, saved_path in enumerate(paths, 1):
        expected_name = FILENAME if ordinal == 1 else f'correction-round-extension-{ordinal:03d}.json'
        receipt = json.loads(saved_path.read_text())
        body = {k:v for k,v in receipt.items() if k != 'artifact_sha256'}
        if (saved_path.name != expected_name or receipt.get('schema_version') != VERSION
                or receipt.get('artifact_sha256') != sha256_json(body)
                or receipt.get('job') != job
                or receipt.get('original_max_correction_rounds') != policy['max_correction_rounds']
                or receipt.get('effective_max_correction_rounds') != policy['max_correction_rounds'] + ordinal
                or receipt.get('previous_extension') != (binding(paths[ordinal-2]) if previous else None)):
            raise ValueError('Route round extension identity or original policy changed')
        check_binding(receipt['authorization'])
        for name, pin in receipt['original_files'].items():
            if Path(name).name != name or binding(root/name) != pin:
                raise ValueError('Route round extension original artifact changed')
        previous = receipt
    if authorization is None:
        return previous
    if previous and binding(authorization) == previous['authorization']:
        return previous
    if root.name != job['job_id'] or (root/'route-cvp-run.json').exists() or (root/'route-change-plan.json').exists():
        raise ValueError('Route round extension requires the original uncommitted job')
    read = lambda name:json.loads((root/name).read_text())
    direction = json.loads(Path(authorization).read_text())
    material = read('cvp-route-materials.json')
    if (material.get('artifact_sha256') != job['material_sha256']
            or material['viewpoint_id'] != job['viewpoint_id']):
        raise ValueError('Route round extension material differs from original job')
    reviews = sorted(root.glob('route-review-[0-9][0-9][0-9].json'))
    initial_validation = not reviews or direction.get('stage') == 'program_validation'
    if initial_validation:
        repairs=sorted(p for p in root.glob('raw-*-validation-[0-9][0-9][0-9].json')
                       if not p.name.startswith('raw-route-review-'))
        limit=previous['effective_max_correction_rounds'] if previous else policy['max_correction_rounds']
        if not repairs or len(repairs)!=limit:
            raise ValueError('Route validation extension requires its exact exhausted repair chain')
        if reviews:
            name=direction.get('validation_response_name','')
            if Path(name).name != name or not name.startswith('raw-route-reconsideration-') or not name.endswith('.json'):
                raise ValueError('Route validation extension needs the exact failed correction response')
            latest=root/name
            if latest.with_name(latest.stem+'-validated.json').exists():
                raise ValueError('Route validation extension cannot extend an already valid response')
        else:
            if (root/'raw-route-proposal-000-validated.json').exists():
                raise ValueError('Route initial validation extension needs an unvalidated initial proposal')
            latest=repairs[-1]
        ordinal=None;review={'review_sha256':sha256_json(read(latest.name))}
        consumed=len(repairs)
    else:
        latest = reviews[-1]
        ordinal = int(latest.stem.rsplit('-',1)[1])
        review = read(latest.name)
        validation = review['validation']
        consumed = ordinal + len(list(root.glob('route-proposal-validation-[0-9][0-9][0-9].json')))
    current_limit = previous['effective_max_correction_rounds'] if previous else policy['max_correction_rounds']
    if (consumed != current_limit or (not initial_validation and (
            validation['outcome'] != 'findings'
            or not validation['reconsideration_required'] or validation['exception_keys']))):
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
    if initial_validation:
        expected['stage']='program_validation' if reviews else 'initial_program_validation'
        if reviews:expected['validation_response_name']=latest.name
    if previous:
        expected['previous_extension_sha256'] = previous['artifact_sha256']
    if direction != expected or not isinstance(direction.get('owner_direction'),str) or not direction['owner_direction'].strip():
        raise ValueError('Route round extension authorization differs from exact job/review/one round')
    receipt = {'schema_version':VERSION, 'job':job, 'authorization':auth_pin,
        'original_max_correction_rounds':policy['max_correction_rounds'],
        'effective_max_correction_rounds':current_limit+1,
        'previous_corrections_consumed':consumed, 'latest_review_round':ordinal,
        'latest_review_sha256':review['review_sha256'], 'human_content_approval':False,
        'original_files':{p.name:binding(p) for p in sorted(root.glob('*.json'))}}
    if initial_validation:
        receipt['stage']=expected['stage']
    if previous:
        receipt['previous_extension'] = binding(paths[-1])
        path = root/f'correction-round-extension-{len(paths)+1:03d}.json'
    receipt['artifact_sha256'] = sha256_json(receipt)
    write_immutable(path, receipt, require_new=True)
    return receipt
