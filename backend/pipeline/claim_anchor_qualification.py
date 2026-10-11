"""Separately review an exact historical anchor defect and bound Evidence use.

An owner scope is not a postimage approval. Uses the ordinary source Runner,
one proposal/independent review/optional correction and common SQL transaction.
Other graph maintenance remains explicitly pending, never newly approved.
"""
from copy import deepcopy
import json
import hashlib

from backend.pipeline import source_reconciliation as c
from backend.api.canonical_repository.postgres_store import sha256_json, record_content_sha

VERSION = 'wang_claim_anchor_qualification_v1'
FRAGMENT_FIELDS = {'schema_version','visibility','source_id','verbatim_excerpt','paragraph_key',
    'media_time','source_url','citation_id','source_sha256','paragraph_text_sha256',
    'verbatim_excerpt_sha256','anchor_state','source_modality'}


def normalized_proposal(frozen,response):
    from backend.pipeline.deferred_claim_repair import decode_proposal
    proposal=c.Proposal.model_validate(decode_proposal(response))
    for row in proposal.records:
        if row.collection!='source_fragments':continue
        value=row.payload;src=frozen['after'][0]
        if value['source_id']!=frozen['manifest']['source_id']:raise ValueError('foreign anchor fragment')
        text=src['paragraphs'].get(value['paragraph_key'],{}).get('text','');quote=value['verbatim_excerpt']
        if not quote or text.count(quote)!=1:raise ValueError('new quote must be exact unique own body')
        value.update(source_sha256=src['body_sha256'],paragraph_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            verbatim_excerpt_sha256=hashlib.sha256(quote.encode()).hexdigest(),anchor_state='source_version_bound')
    return proposal


def direction(manifest, states, snapshots=None):
    d = json.loads(c.bound_bytes(manifest.anchor_qualification, snapshots))
    if (d.get('schema_version') != VERSION or d.get('authorized_by') != 'owner'
            or not d.get('authorization_quote') or d.get('human_content_approval') is not False
            or d.get('artifact_sha256') != sha256_json({k:v for k,v in d.items() if k!='artifact_sha256'})
            or d.get('source_id') != manifest.source_id or d.get('maximum_corrections') != 1):
        raise ValueError('anchor qualification lacks exact owner revision scope')
    ck, ek = c.key('claims',d['claim_id']),c.key('evidence_steps',d['evidence_id'])
    claim, evidence = states[ck], states[ek]
    if (claim.get('review_status') != 'human_review_required'
            or claim.get('reviewed_by') != 'independent_ai_review'
            or evidence.get('support_eligibility') != 'withheld_unreviewed'
            or evidence.get('review_status') != 'independent_terminal_reviewed'
            or evidence.get('produced_claim_ids') != [d['claim_id']]
            or d['evidence_id'] not in claim['withheld_evidence_step_ids']
            or d['evidence_id'] not in claim['evidence_step_ids']):
        raise ValueError('not the exact independently flagged anchor/bound Evidence generation')
    for k, p in ((ck,claim),(ek,evidence)):
        if d['before_pins'].get(k) != [p['revision'],record_content_sha(p),False]:
            raise ValueError('anchor qualification current version/hold proof drift')
    review=json.loads(c.bound_bytes(d['original_review'],snapshots))
    rows=[r for r in review['claim_reviews'] if r['claim_id']==d['claim_id']]
    if (len(rows)!=1 or rows[0]['decision']!='changes_suggested'
            or rows[0].get('human_review_reason') or rows[0].get('proposed_statement')
            or len(rows[0]['issues'])!=1 or rows[0]['issues'][0]['issue_type']!='insufficient_anchor'
            or rows[0]['issues'][0]['severity']!='low'):
        raise ValueError('scope cannot clear substantive/human content holds')
    count=sum(a.get('proposed_highlight',{}).get('text')==d['withdraw_anchor_quote']
              for o in claim['occurrences'] for a in o.get('anchors',[]))
    if count!=1:raise ValueError('exact defective anchor missing/ambiguous')
    expected={ck:{'occurrences','eligible_evidence_step_ids','withheld_evidence_step_ids'},
              ek:{'support_eligibility','valid_for_identity_review'}}
    for eid in d.get('anchor_binding_evidence_ids',[]):
        k=c.key('evidence_steps',eid);ev=states[k]
        if (eid not in claim['evidence_step_ids'] or d['claim_id'] not in ev['produced_claim_ids']
                or ev.get('review_status') in c.HUMAN_HOLDS|{'human_hold','rejected','withheld_unreviewed'}
                or d['before_pins'].get(k)!=[ev['revision'],record_content_sha(ev),False]):
            raise ValueError('existing anchor binding repair is foreign/stale/held')
        expected[k]={'source_fragment_id','source_fragment_ids'}
    creates=[p for p in manifest.permissions if p.create]
    if ({p.key:set(p.fields) for p in manifest.permissions if not p.create}!=expected
            or len(creates)>1 or any(p.collection!='source_fragments' or p.parent_id!=manifest.source_id
                or set(p.fields)!=FRAGMENT_FIELDS for p in creates)):
        raise ValueError('anchor qualification may not edit Claim/Evidence words or new identities')
    return d


def validate_delta(frozen, old, new, key):
    manifest=c.Manifest.model_validate(frozen['manifest'])
    if not manifest.anchor_qualification:return False
    d=direction(manifest,c.index(frozen['before_records']),frozen.get('physical_snapshots'))
    ck,ek=c.key('claims',d['claim_id']),c.key('evidence_steps',d['evidence_id'])
    repairs={c.key('evidence_steps',eid) for eid in d.get('anchor_binding_evidence_ids',[])}
    if key.startswith('source_fragments/'):
        if old or new.get('source_id')!=manifest.source_id or new.get('anchor_state')!='source_version_bound':
            raise ValueError('anchor binding may only add precise own fragments, not edit history')
        quote=new.get('verbatim_excerpt');text=frozen['after'][0]['paragraphs'].get(new.get('paragraph_key'),{}).get('text','')
        if not quote or text.count(quote)!=1:raise ValueError('new anchor fragment is not exact unique own text')
        return False
    if key in repairs:
        fields={'source_fragment_id','source_fragment_ids'}
        if ({k:v for k,v in c._semantic(old).items() if k not in fields}
                != {k:v for k,v in c._semantic(new).items() if k not in fields}):
            raise ValueError('anchor binding repair may not change Evidence content or qualification')
        return False
    fields={'occurrences','eligible_evidence_step_ids','withheld_evidence_step_ids'} if key==ck else {'support_eligibility','valid_for_identity_review'}
    if key not in {ck,ek} or ({k:v for k,v in c._semantic(old).items() if k not in fields}
            != {k:v for k,v in c._semantic(new).items() if k not in fields}):
        raise ValueError('anchor qualification changed unrelated words/ownership/binding')
    if key==ck:
        expected=deepcopy(old['occurrences'])
        for o in expected:
            o['anchors']=[a for a in o.get('anchors',[]) if a.get('proposed_highlight',{}).get('text')!=d['withdraw_anchor_quote']]
        if new['occurrences']!=expected:
            raise ValueError('only the exact erroneous anchor can be withdrawn; retain every other occurrence')
        for f,wanted in [('eligible_evidence_step_ids',set(old['eligible_evidence_step_ids'])|{d['evidence_id']}),
                         ('withheld_evidence_step_ids',set(old['withheld_evidence_step_ids'])-{d['evidence_id']})]:
            if set(new[f])!=wanted or len(new[f])!=len(wanted):raise ValueError('unrelated Evidence bucket change')
    else:
        if new.get('support_eligibility')!='eligible_with_label' or new.get('valid_for_identity_review') is not True:
            raise ValueError('qualification must be explicit, independently reviewed eligible use')
        post=c.index(frozen['before_records'])
        sources={frozen['manifest']['source_id']:frozen['after'][0],**{s['source_id']:s for s in frozen['context_sources']}}
        ids=set(old.get('source_fragment_ids',[]))|({old['source_fragment_id']} if old.get('source_fragment_id') else set())
        if not ids:raise ValueError('qualified Evidence has no bound own source')
        for fid in ids:
            frag=post[c.key('source_fragments',fid)];src=sources[frag['source_id']]
            text=src['paragraphs'][frag['paragraph_key']]['text']
            if len(c.containing_anchor_fragments([frag],frag['verbatim_excerpt'],text,frag['source_id'],frag['paragraph_key'],src['body_sha256']))!=1:
                raise ValueError('qualified own Evidence physical body/quote proof failed')
    return key==ck


def inventory(manifest, states, snapshots=None):
    d=direction(manifest,states,snapshots)
    affected=[c.key('claims',d['claim_id']),c.key('evidence_steps',d['evidence_id']),
              *[c.key('evidence_steps',eid) for eid in d.get('anchor_binding_evidence_ids',[])]]
    return {'affected_keys':sorted(affected),'context_keys':sorted(set(states)-set(affected)),'unknown_keys':[]}


def validate_binding_closure(frozen, proposal, post):
    """New quotes must realize existing own anchors, not unrelated new Evidence."""
    manifest=c.Manifest.model_validate(frozen['manifest'])
    d=direction(manifest,c.index(frozen['before_records']),frozen.get('physical_snapshots'))
    claim=post[c.key('claims',d['claim_id'])];owned=set(d.get('anchor_binding_evidence_ids',[]))
    fragments=set()
    for o in claim['occurrences']:
        if o['transcript_id']!=post[c.key('source_documents',manifest.source_id)]['transcript_id']:continue
        for a in o.get('anchors',[]):
            eid=c.anchor_evidence_id(claim,o,a)
            if eid not in owned:continue
            ev=post[c.key('evidence_steps',eid)]
            ids=set(ev.get('source_fragment_ids',[]))|({ev['source_fragment_id']} if ev.get('source_fragment_id') else set())
            q=a['proposed_highlight']['text']
            matches=[fid for fid in ids if post[c.key('source_fragments',fid)]['paragraph_key']==a['paragraph_key']
                and q in post[c.key('source_fragments',fid)]['verbatim_excerpt']]
            if len(matches)!=1:raise ValueError('anchor binding repair must realize every retained own anchor exactly')
            fragments.update(matches)
    created={r.object_id for r in proposal.records if r.collection=='source_fragments'}
    if not created<=fragments:raise ValueError('new anchor fragment is unrelated/orphan')
