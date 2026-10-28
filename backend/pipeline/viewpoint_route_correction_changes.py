"""Expand changed proposal fields before the existing complete Route checks."""
from copy import copy, deepcopy
from pathlib import Path
from typing import Literal

from pydantic import Field
from backend.api.canonical_repository.cvp_management import _json_fields
from backend.api.canonical_repository.viewpoint_batch_resolution import StrictBatchModel
from backend.api.canonical_repository.viewpoint_observation_support import FindingDispositionV3
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact

VERSION = 'wang_route_correction_changes_v1'
MARKER = 'route_correction_output_contract'
PROPOSAL_VERSION = 'wang_route_proposal_changes_v1'
PROPOSAL_MARKER = 'route_proposal_output_contract'
CREATION_SHAPES = 'route_creation_field_schemas'
COLLECTIONS = {
    'argument_route_candidates': ('local_route_key',),
    'source_route_attestations': ('local_attestation_key',),
    'viewpoints_with_no_route': ('viewpoint_revision_id',),
    'unattested_members': ('conclusion_viewpoint_revision_id', 'source_id'),
    'observation_supports': ('local_observation_key',),
    'sources_without_observation': ('viewpoint_revision_id', 'source_id'),
    'retired_route_revision_ids': (),
    'retired_observation_support_ids': (),
}


class ProposalEdit(StrictBatchModel):
    collection: Literal['argument_route_candidates', 'source_route_attestations',
        'viewpoints_with_no_route', 'unattested_members', 'observation_supports',
        'sources_without_observation', 'retired_route_revision_ids', 'retired_observation_support_ids']
    key: str = Field(min_length=1)
    action: Literal['update', 'create', 'remove']
    fields_json: str | None = Field(description='Only changed top-level fields for update; complete business fields for create; null for remove or retirement-ID edits.')


class CorrectionChanges(StrictBatchModel):
    schema_version: Literal['wang_route_correction_changes_v1'] = VERSION
    finding_dispositions: list[FindingDispositionV3] = Field(min_length=1)
    edits: list[ProposalEdit]


class ProposalChanges(StrictBatchModel):
    schema_version: Literal['wang_route_proposal_changes_v1'] = PROPOSAL_VERSION
    edits: list[ProposalEdit]


def with_creation_shapes(packet):
    """Empty collections have no examples; supply their actual business shape."""
    baseline=(packet.get('route_proposal_baseline') or packet.get('route_proposal')
              or packet.get('route_proposal_context'))
    if not baseline:
        return packet
    from backend.api.canonical_repository.viewpoint_observation_support import ArgumentRouteProposalV3Response
    from backend.api.canonical_repository.viewpoint_batch_resolution import ArgumentRouteProposalV2Response
    model=ArgumentRouteProposalV3Response if 'observation_supports' in baseline else ArgumentRouteProposalV2Response
    schema=model.model_json_schema();definitions=schema.get('$defs',{});shapes={}
    for name,identity in COLLECTIONS.items():
        if name not in baseline or baseline[name] or not identity:
            continue
        item=deepcopy(schema['properties'][name]['items'])
        if '$ref' in item:item=deepcopy(definitions[item['$ref'].rsplit('/',1)[1]])
        supplied=set(identity) if len(identity)==1 else set()
        if name=='source_route_attestations':supplied.add('source_revision_sha256')
        item['properties']={k:v for k,v in item.get('properties',{}).items() if k not in supplied}
        item['required']=[k for k in item.get('required',[]) if k not in supplied]
        shapes[name]=item
    if not shapes:return packet
    used={}
    def collect(value):
        if isinstance(value,dict):
            if '$ref' in value:
                name=value['$ref'].rsplit('/',1)[1]
                if name not in used:
                    used[name]=deepcopy(definitions[name]);collect(used[name])
            for child in value.values():collect(child)
        elif isinstance(value,list):
            for child in value:collect(child)
    collect(shapes)
    return packet | {CREATION_SHAPES:{'collections':shapes,'$defs':used,
        'instruction':'新建空集合的条目时，fields_json必须使用相应集合的实际字段定义。程序拥有的local key及source SHA已省略，不需抄写。不要猜测字段名；这些是格式说明，不是新证据或内容批准。'}}


PROMPT = '''你是同一 CVP 的论证层修改员。使用 wang_route_correction_changes_v1。
只返回 finding_dispositions 和 edits，不返回 revised_proposal、不抄写未修改记录、原文或 SHA。
逐项回应当前全部 correct 异议。接受的说明实际改法；不同意时说明原文依据，沿既有 rebutted/deferred 规则处理。
edits 每项含 collection、key、action、fields_json。key 是该集合现有的 local key；
viewpoints_with_no_route 用 viewpoint_revision_id；unattested_members 用 conclusion_viewpoint_revision_id::source_id；
sources_without_observation 用 viewpoint_revision_id::source_id；两个 retired_*_ids 集合直接用原 ID。
update 只给实际变化的顶层字段，fields_json 为 JSON 对象字符串；未给字段由程序原样继承。
create 给完整新业务字段；单字段 key 由程序填入，不回抄；双字段 key 的字段须与 key 一致。
新来源实例的 source_revision_sha256 由程序按 source_id 补齐，不输出它。
remove 只撤去当前未提交提案中的条目，不删除数据库记录，fields_json=null。
retired_*_ids 只用 create/remove，fields_json=null。修改与旧记录退役不同，必须明确选择。
同一集合的同一 key 只能出现一次；不能用 update 更换 key。空 edits 不会获得通过。
只报告解决异议及必要依赖调整的实际修改；不得重写无关路线、实例、观察或既有正确绑定。
原材料的 CVP、来源、证据资格、单来源实例、有效终端、观察与推理桥梁判准全部保持。
不能改 scope 或批准的 CVP 集合，也不能修改 Claim 或 Claim–CVP 连接。
程序合成完整结果后，先进行全部原有校验，再交 Opus 完整审核；只输出改动不缩小审核范围。
'''


def correction_adapter(adapter, packet_path: Path, response_path: Path):
    """Only undispatched stages select the new output; cached stages stay exact."""
    if packet_path.exists():
        if read_artifact(packet_path).get(MARKER) != VERSION:
            return adapter
    elif any(p.exists() for p in (response_path,
            response_path.with_name(response_path.stem+'.call-intent.json'),
            response_path.with_name(response_path.stem+'.request-encoding.json'))):
        return adapter
    if not hasattr(adapter, '_response_model'):
        return adapter  # Legacy injected transports retain their declared schema.
    result = copy(adapter)
    result._response_model = CorrectionChanges
    result._schema_name = VERSION
    result._base_prompt = result._prompt = PROMPT
    result.prompt_sha256 = sha256_json({'prompt': PROMPT})
    result.generation_config_sha256 = sha256_json({
        'base_config_sha256': adapter.generation_config_sha256, 'route_correction_output': VERSION})
    return result


def proposal_adapter(adapter, packet_path: Path, response_path: Path):
    if packet_path.exists():
        if read_artifact(packet_path).get(PROPOSAL_MARKER) != PROPOSAL_VERSION:
            return adapter
    elif any(p.exists() for p in (response_path,
            response_path.with_name(response_path.stem+'.call-intent.json'),
            response_path.with_name(response_path.stem+'.request-encoding.json'))):
        return adapter
    if not hasattr(adapter, '_response_model'):
        return adapter
    result = copy(adapter)
    result._response_model = ProposalChanges
    result._schema_name = PROPOSAL_VERSION
    result._base_prompt = result._prompt = adapter._base_prompt + '''
输出协议 wang_route_proposal_changes_v1 取代上述完整输出和响应 schema 约定：
只返回 edits，操作格式与如下说明一致；不返回完整提案、未变记录、SHA 或 finding_dispositions。
route_proposal_baseline 是旧记录的机械投影草稿，绝不代表它们仍然正确或已通过本轮审核。
逐项核对全部旧骨架、实例、观察、当前 Claim 和原文，明确需要修改、撤去或补充的内容。
旧结论版本由程序映射到当前 CVP；绑定为 null 或已失效时必须修正，不能猜测或默认批准。
未变部分由程序保留。空 edits 也要完整交 Opus 审核；新材料确有新路线或观察须补齐。
''' + PROMPT[PROMPT.index('edits 每项'):PROMPT.index('只报告解决异议')].replace('空 edits 不会获得通过。', '')
    result.prompt_sha256 = sha256_json({'prompt': result._prompt})
    result.generation_config_sha256 = sha256_json({
        'base_config_sha256': adapter.generation_config_sha256, 'route_proposal_output': PROPOSAL_VERSION})
    return result


def expand_correction(raw, proposal, review, route_packet, *, expected_contract=None):
    """No inferred edits: omitted records and fields remain untouched."""
    if expected_contract is not None and (expected_contract != VERSION or raw.get('schema_version') != VERSION):
        raise ValueError('Route correction response differs from its compact output contract')
    if raw.get('schema_version') != VERSION:
        return raw
    changes = CorrectionChanges.model_validate(raw)
    revised = type(proposal).model_validate(apply_edits(changes.edits, proposal.model_dump(mode='json'), route_packet))
    return {'schema_version': 'wang_argument_route_reconsideration_v3' if hasattr(proposal, 'observation_supports') else 'wang_argument_route_reconsideration_v2',
        'route_proposal_sha256': sha256_json(proposal.model_dump(mode='json')),
        'route_review_sha256': sha256_json(review.model_dump(mode='json')),
        'finding_dispositions': [d.model_dump(mode='json') for d in changes.finding_dispositions],
        'revised_proposal': revised.model_dump(mode='json')}


def expand_proposal(raw, baseline, route_packet):
    changes = ProposalChanges.model_validate(raw)
    return apply_edits(changes.edits, baseline, route_packet)


def apply_edits(edits, baseline, route_packet):
    payload = deepcopy(baseline)
    seen = set()
    for edit in edits:
        collection, key = edit.collection, edit.key
        if collection not in payload or (collection, key) in seen:
            raise ValueError('Route edit collection absent or target repeated')
        seen.add((collection, key))
        identity = COLLECTIONS[collection]
        rows = payload[collection]
        def row_key(row):
            return '::'.join(row[field] for field in identity) if identity else row
        matches = [i for i, row in enumerate(rows) if row_key(row) == key]
        if len(matches) > 1:
            raise ValueError('Route edit target is ambiguous')
        if edit.action == 'remove':
            if not matches or edit.fields_json is not None:
                raise ValueError('Route removal needs an existing target and no fields')
            rows.pop(matches[0])
            continue
        if (edit.action == 'create' and matches) or (edit.action == 'update' and not matches):
            raise ValueError('Route create/update target does not match current proposal')
        if not identity:
            if edit.action != 'create' or edit.fields_json is not None:
                raise ValueError('Route retirement IDs only accept create/remove without fields')
            rows.append(key)
            rows.sort()
            continue
        fields = _json_fields(edit.fields_json)
        if edit.action == 'update':
            if set(fields) & set(identity):
                raise ValueError('Route edit cannot rewrite its key')
            original = rows[matches[0]]
            if all(original.get(k) == v for k, v in fields.items()):
                raise ValueError('Unchanged Route fields must be omitted')
            item = deepcopy(original) | fields
        else:
            item = fields
            if len(identity) == 1:
                if identity[0] in fields:
                    raise ValueError('Route creation key is program supplied')
                item[identity[0]] = key
        if row_key(item) != key:
            raise ValueError('Route creation key differs from its fields')
        if collection == 'source_route_attestations':
            if 'source_revision_sha256' in fields:
                raise ValueError('Route source SHA is program supplied')
            item['source_revision_sha256'] = route_packet['source_revisions'][item['source_id']]
        if edit.action == 'update': rows[matches[0]] = item
        else: rows.append(item)
    return payload
