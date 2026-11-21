import json, sys
R = lambda a, b: set(range(a, b + 1))
KEYS = {
 'g03': {'units': {'s2 inclusio→CL2': R(18, 30), 's3 中間各福→CL1': R(31, 48), 's4 反方(哀慟)': R(52, 56), 's4 回答': R(57, 58),
                   's5 時態→CL4': R(61, 77) | {79}, 's6a 天國只在馬太': {82}, 's6a2 猶太人以天代神': {83, 89}, 's6b 路十五→CL5': R(84, 88), 's7 趕鬼→CL6': R(97, 101), 's8 已然未然解釋時態→CL4': R(105, 109)},
         'claim': R(49, 51) | {80} | R(91, 96) | R(102, 104)},
 'g01': {'units': {'第一大段+有福答案→CL2': R(9, 14), '第二大段+因為→CL3': R(16, 18), '背景': R(1, 4), '五13–16/17–七29': {21, 22}},
         'claim': {7, 8, 15, 19, 20}},
 'g02': {'units': {'a 原文事實 ptochos': R(2, 6), 'a 馬太用同字→CL3': R(8, 10), 'b 賽61:1-2→CL2': R(11, 16), 'c1 賽61:7↔恩典': R(21, 24),
                   'c2 太5:5↔賽61:7': R(29, 33), 'd 逼迫→CL4': R(37, 49), 'e 故事→CL5': R(61, 93), '反方': {94}, '回答': {95, 96, 97}},
         'claim': {19, 20} | R(50, 60) | R(97, 100)},
}
g, f = sys.argv[1], sys.argv[2]
K = KEYS[g]; out = json.load(open(f))
n = lambda s: int(s.split('#')[1])
errs = 0
hits = {u: [] for u in K['units']}
for st in out['steps']:
    cov = {n(s) for s in st['sentence_ids']}
    us = [u for u, v in K['units'].items() if len(cov & v) * 3 >= len(v) or (len(v) <= 2 and cov & v)]
    for u in us: hits[u].append(st['step_id'])
    flag = ''
    if len(us) > 1: flag = '  MERGED'; errs += 1
    if not us: flag = '  CLAIM TEXT AS STEP' if cov & K['claim'] else '  NO KEY STEP'; errs += flag.endswith('STEP') and 'CLAIM' in flag
    print(f"  {st['step_id']:4} #{min(cov)}–{max(cov)} {st['line'][:60]}{flag}")
for u, h in hits.items():
    tag = '' if len(h) == 1 else ('  SPLIT' if h else '  MISSED')
    if tag: errs += 1
    print(f"{u}: {h}{tag}")
print('ERRORS', errs)
