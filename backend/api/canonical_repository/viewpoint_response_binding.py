"""Derive protocol echo metadata from an authenticated request, not model text."""
from copy import deepcopy

VERSION = 'wang_program_response_binding_v1'
PROMPT = '\n## 程序管理版本绑定\n响应 schema 不要求返回的 SHA 字段由程序从本次精确输入绑定。不要抄写 proposal_sha256、review_sha256 或 coverage_claim_sha256。按原对象 ID、Claim ID 和成分编号返回完整决定、理由、证据和内容。此前模板要求抄写这些 SHA 的指令由本协议取代；不改变任何内容判断规则。\n'


def fields(model):
    name = model.__name__
    if model.__module__ == 'backend.api.canonical_repository.viewpoint_batch_resolution':
        if name in {'CanonicalViewpointReviewResponse', 'CoverageCanonicalViewpointReviewResponse'}:
            return ('proposal_sha256',), name == 'CoverageCanonicalViewpointReviewResponse'
        if name == 'CanonicalViewpointReconsiderationResponse':
            return ('proposal_sha256', 'review_sha256'), False
    if model.__module__ == 'backend.pipeline.source_reconciliation' and name == 'Review':
        return ('proposal_sha256',), False
    return (), False


def wire_schema(model):
    schema = deepcopy(model.model_json_schema())
    top, coverage = fields(model)
    def remove(node, names):
        for name in names:
            node.get('properties', {}).pop(name, None)
        if 'required' in node:
            node['required'] = [name for name in node['required'] if name not in names]
    remove(schema, top)
    if coverage:
        remove(schema['$defs']['CoverageReviewedChange'], ['coverage_claim_sha256'])
    return schema


def bind_response(model, response, request):
    """Only echo fields change; caller must first authenticate request/raw."""
    top, coverage = fields(model)
    result = deepcopy(response)
    for name in top:
        value = request.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError('program response binding lacks exact request ' + name)
        result[name] = value
    if coverage:
        claims = request.get('packet', request).get('claims', [])
        expected = {c['claim_id']:c['claim_revision_sha256'] for c in claims}
        if not expected or len(expected) != len(claims):
            raise ValueError('program response binding requires unique pinned Claims')
        rows = result.get('change_reviews', [])
        identities = [(r.get('claim_id'), r.get('component_index')) for r in rows]
        if len(identities) != len(set(identities)):
            raise ValueError('program response binding has duplicate review identities')
        for row in rows:
            if row.get('claim_id') not in expected:
                raise ValueError('program response binding references foreign Claim')
            row['coverage_claim_sha256'] = expected[row['claim_id']]
    return result
