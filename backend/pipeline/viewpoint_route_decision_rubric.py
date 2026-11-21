"""One small decision rubric for new proposer, correction and review inputs."""
from copy import deepcopy

MARKER = 'route_decision_rubric'
RUBRIC = {
    'schema_version': 'wang_route_decision_rubric_v1',
    'instruction': ('以下是判定示例，不是本次来源、证据或预定答案。只用本次绑定材料作判断；'
        'GPT 提议、修改与 Opus 审核使用同一标准。程序校验通过不代表内容通过。'),
    'cases': [
        {'case': 'observation_without_exhaustive_proof',
         'example': '来源指出本节称号有定冠词，并用它解释该称号；没有逐节穷举全部用例。',
         'decision': '保存实际词形观察及用途，范围限于来源所说；不得因未穷举而删除观察，也不得补成全称证明。'},
        {'case': 'conclusion_only',
         'example': '来源只重复结论，没有指出具体文本特征，也没有连接证据与结论的解释。',
         'decision': '不把结论改写成观察或推理路线；可以没有路线和观察。'},
        {'case': 'partial_source_support',
         'example': '同篇有可绑定证据支持既有骨架的部分步骤，终端有本 CVP 当前修订的正向支持连接。',
         'decision': '可保存 partial 实例，如实标明缺少的步骤；不能仅因不是等价结论成员而排除全部论据。'},
        {'case': 'no_cross_source_full_attestation',
         'example': '甲篇有前提，乙篇有推理桥梁，各自都不足以覆盖全部必需步骤。',
         'decision': '不能拼成任一篇的 full 实例；逐篇保存实际支持范围，不发明来源没有的桥梁。'},
        {'case': 'same_skeleton_different_evidence',
         'example': '同一结论和承重推理结构在不同来源重复，使用的经文或例子有取舍。',
         'decision': '优先作为同一骨架的逐篇实例；保留各篇实际经文和解释，只有承重前提或推理结构实质不同才拆路线。'},
    ],
}


def with_decision_rubric(packet):
    return packet | {MARKER: deepcopy(RUBRIC)}
