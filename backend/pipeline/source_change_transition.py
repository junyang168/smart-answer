"""Exact source-version handoff; mechanical bindings never confer content approval."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from backend.pipeline import source_reconciliation as source
from backend.pipeline.cvp_sql_commit import binding, check_binding, pins, pin_rows, decoded_pins
from backend.pipeline.claim_management_intake import immutable
from backend.pipeline.source_projection import project_script
from backend.pipeline.source_reconciliation_runner import archive_source_version

VERSION = 'wang_source_change_transition_v1'
PROJECTION_FIELDS = {
    'source_body_sha256': 'body_sha256', 'source_text_sha256': 'spoken_text_sha256',
    'editorial_structure_sha256': 'editorial_structure_sha256',
    'editorial_topology_sha256': 'editorial_topology_sha256',
}


def projection(bound):
    raw = source.verify_file(source.FileBinding.model_validate(bound)).read_bytes()
    value = json.loads(raw)
    return project_script(value['script'] if isinstance(value, dict) else value)


def verify_before(document, bound, *, recovered=False):
    check_binding(bound)
    view = projection(bound)
    if view.visual_blocks:
        raise ValueError('source change visual assets require their attested source adapter')
    if recovered:
        # Lost formatting bytes cannot be reconstructed from a body hash. All
        # registered semantic projections must agree; record the real file SHA.
        if any(not document.get(f) or document[f] != getattr(view, attr)
               for f, attr in PROJECTION_FIELDS.items()):
            raise ValueError('recovered source must prove every registered projection')
    elif document.get('source_file_sha256') != bound['file_sha256']:
        raise ValueError('before source is not the registered physical version')
    for field, attr in PROJECTION_FIELDS.items():
        if document.get(field) and document[field] != getattr(view, attr):
            raise ValueError('registered before source projection drift: ' + field)
    for field in ('source_sha256', 'anchor_binding_sha256'):
        if document.get(field) and document[field] != view.body_sha256:
            raise ValueError('registered body binding drift: ' + field)
    return view


def inventory(store, sid):
    """One indexed own-source inventory; no Claim-relation traversal."""
    with store.connect() as conn:
        ids = conn.execute("""SELECT object_id FROM wang_knowledge.objects
            WHERE collection='source_fragments' AND retired_at IS NULL
              AND payload @> %s::jsonb ORDER BY object_id""", (json.dumps({'source_id': sid}),)).fetchall()
    states = {('source_documents', sid): store.get_record_state('source_documents', sid)}
    states.update({('source_fragments', oid): store.get_record_state('source_fragments', oid) for (oid,) in ids})
    if not states['source_documents', sid] or states['source_documents', sid]['retired']:
        raise ValueError('current source is missing or retired')
    from backend.pipeline.claim_management import HELD
    if states['source_documents', sid]['payload'].get('review_status') in HELD:
        raise ValueError('source has a protected review hold; explicit resolution required')
    return states


def compile_package(intent):
    if intent.get('schema_version') != VERSION:
        raise ValueError('explicit source change intent required')
    doc = intent['records']['source_documents/' + intent['source_id']]['payload']
    old = verify_before(doc, intent['before'], recovered=intent['recovered_baseline'])
    current = projection(intent['current'])
    if current.visual_blocks:
        raise ValueError('source change visual assets require their attested source adapter')
    previous = source.source_snapshot(intent['before'], source=doc)
    now = source.source_snapshot(intent['current'], source=doc)
    # Existing body locators must retain their identity. Ambiguous structural
    # edits need a mapping, never a guessed paragraph offset.
    if (set(previous['paragraphs']) != set(now['paragraphs']) or any(
        {k:v for k,v in row.items() if k != 'text'} !=
        {k:v for k,v in now['paragraphs'][key].items() if k != 'text'}
        for key, row in previous['paragraphs'].items())):
        raise ValueError('source paragraph coordinates changed; explicit locator mapping required')
    changed = [key for key in now['paragraphs'] if previous['paragraphs'][key]['text'] != now['paragraphs'][key]['text']]
    if not changed:
        raise ValueError('no professor-source text change; use the editorial transition')
    post = deepcopy(doc)
    if not post.get('locator_space'):
        post['locator_space'] = 'spoken_body_v1'
    post.update(source_file_sha256=intent['current']['file_sha256'], source_sha256=current.body_sha256,
                source_body_sha256=current.body_sha256, source_text_sha256=current.spoken_text_sha256,
                anchor_binding_sha256=current.body_sha256,
                editorial_structure_sha256=current.editorial_structure_sha256,
                editorial_topology_sha256=current.editorial_topology_sha256)
    package = {'package_id': 'SOURCE-CHANGE-' + source.sha256_json(intent)[:24], 'source_documents': [post]}
    repairs = []
    for key, state in intent['records'].items():
        if not key.startswith('source_fragments/'):
            continue
        value = state['payload']; pkey = value.get('paragraph_key')
        quote = value.get('verbatim_excerpt')
        if (pkey not in previous['paragraphs'] or not quote
                or quote not in previous['paragraphs'][pkey]['text']
                or value.get('source_sha256') != old.body_sha256):
            repairs.append(key)
            continue
        if quote not in now['paragraphs'][pkey]['text']:
            repairs.append(key)  # GPT repairs wording; mechanics never guess.
            continue
        fragment = deepcopy(value)
        fragment.update(source_sha256=current.body_sha256,
            paragraph_text_sha256=hashlib.sha256(now['paragraphs'][pkey]['text'].encode()).hexdigest(),
            verbatim_excerpt_sha256=hashlib.sha256(quote.encode()).hexdigest())
        if fragment != value:
            package.setdefault('source_fragments', []).append(fragment)
    return package, {'changed_paragraph_keys': changed, 'fragment_repair_keys': repairs,
                     'claim_content_approval': False, 'claim_qualification_granted': False}


def validate_package(package):
    app = package.get('source_repair_application', {})
    if set(app) != {'schema_version', 'intent'} or app.get('schema_version') != VERSION:
        raise ValueError('source handoff authority changed')
    check_binding(app['intent'])
    intent = json.loads(Path(app['intent']['path']).read_text())
    expected, _ = compile_package(intent)
    if package != {**expected, 'source_repair_application': app}:
        raise ValueError('source handoff exceeds exact mechanical bindings')
    return intent


def assert_current(store, cursor, package):
    """Called inside the ordinary apply transaction under its advisory lock."""
    intent = validate_package(package)
    current = inventory_in_transaction(cursor, intent['source_id'])
    expected = {source.split_key(k): state for k, state in intent['records'].items()}
    if set(current) != set(expected):
        raise ValueError('source handoff fragment inventory changed')
    store._assert_transaction_read_pins(cursor, pins(expected))
    check_binding(intent['current'])
    if intent.get('source_confirmation'):
        from backend.pipeline import claim_human_source as human
        now = source.source_snapshot(intent['current'])
        _, summary = compile_package(intent)
        entries = [human.entry(package['source_repair_application']['intent'],
            {'user_confirmation': intent['source_confirmation'], 'before_source': intent['before'],
             'current_source': intent['current'], 'claim_content_approval': False},
            intent['source_id'], now, [key], 'confirmation') for key in summary['changed_paragraph_keys']]
        human.register(cursor, entries, origin=package['package_id'])


def inventory_in_transaction(cursor, sid):
    rows = cursor.execute("""SELECT collection,object_id FROM wang_knowledge.objects
        WHERE retired_at IS NULL AND ((collection='source_documents' AND object_id=%s)
          OR (collection='source_fragments' AND payload @> %s::jsonb))""",
        (sid, json.dumps({'source_id': sid}))).fetchall()
    return set(rows)


def register(store, sid, before, root, *, instruction, recovered=False, source_confirmation=None):
    """Crash-safe intent/plan/commit/readback; same root never changes epochs."""
    from backend.pipeline.cvp_sql_commit import plan_from_document
    from backend.pipeline import claim_management as cm
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    intent_path = root / 'intent.json'
    if intent_path.exists():
        intent = json.loads(intent_path.read_text())
        if (intent['source_id'] != sid or intent['instruction'] != instruction
                or intent['recovered_baseline'] != recovered or intent['requested_before'] != before
                or intent.get('source_confirmation') != source_confirmation):
            raise ValueError('source handoff root cannot reset its original authority')
    else:
        records = inventory(store, sid)
        doc = records['source_documents', sid]['payload']
        verify_before(doc, before, recovered=recovered)
        archived = archive_source_version(source.FileBinding.model_validate(before), root / 'versions')
        current = binding(Path(doc['source_path']).resolve())
        preserved = archive_source_version(source.FileBinding.model_validate(current), root / 'versions')
        intent = {'schema_version': VERSION, 'source_id': sid, 'instruction': instruction, 'source_confirmation': source_confirmation,
            'recovered_baseline': recovered, 'requested_before': before, 'before': archived,
            'current': current, 'current_archive': preserved,
            'records': {source.key(*key): state for key, state in records.items()}}
        compile_package(intent)
        immutable(intent_path, intent)
    package, summary = compile_package(intent)
    package['source_repair_application'] = {'schema_version': VERSION, 'intent': binding(intent_path)}
    plan_path = root / 'plan.json'
    if not plan_path.exists():
        plan = store.plan_package(package, source_kind=VERSION)
        immutable(plan_path, plan.as_dict())
    plan = plan_from_document(json.loads(plan_path.read_text()))
    history = store.get_change_history(plan.change_set_id)
    if not history or history['status'] != 'applied':
        store.apply_plan(plan, metadata={'actor': 'source-change-automation', 'intent': binding(intent_path),
            'claim_content_approval': False, 'claim_qualification_granted': False})
    state = store.get_record_state('source_documents', sid)
    operation = next(op for op in plan.operations if op.collection == 'source_documents')
    if [state['revision'], state['content_sha256']] != [operation.after_revision, operation.after_sha256]:
        raise ValueError('source handoff postimage is no longer current')
    check_binding(intent['current'])
    data = {'schema_version': 'wang_claim_source_transition_v3',
        'before_sources': {sid: intent['before']}, 'source_change_set_ids': {sid: plan.change_set_id},
        'recovered_baselines': {sid: binding(intent_path)} if recovered else {}}
    transition = immutable(root / 'transition.json', data)
    return {**summary, 'source_transition': transition, 'source': intent['current'],
            'change_set_id': plan.change_set_id, 'source_id': sid,
            'registered_before_file_sha256': intent['records']['source_documents/' + sid]['payload']['source_file_sha256'],
            'observed_before_file_sha256': intent['before']['file_sha256'], 'recovered_baseline': recovered}
