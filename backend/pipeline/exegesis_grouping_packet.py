"""Lossless, model-readable packet interning. No summaries or context selection.

Long repeated strings are stored once. A {"$text": index} value resolves to
texts[index]. All original keys, values, list order and source text survive.
"""
from __future__ import annotations
from collections import Counter
import json

FORMAT = 'wang_exegesis_interned_packet_v1'
INSTRUCTION = ('输入可能是 wang_exegesis_interned_packet_v1 无损 packet：texts 是字符串表，'
               'data 中仅含 "$text" 的对象引用 texts 对应的零起始索引。先按引用读取完整材料。'
               '这是重复文本去重，不是摘要。model_text_parts 按原文顺序保存独立文字片段；'
               'svg_excluded 标记的图形不提供给模型，不能推断其内容，不能跨该标记拼接逐字引文。\n')


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def pack(payload):
    counts = Counter()
    def count(value):
        if isinstance(value, str):
            if len(value.encode('utf-8')) >= 64:
                counts[value] += 1
        elif isinstance(value, list):
            for item in value: count(item)
        elif isinstance(value, dict):
            if '$text' in value:
                raise ValueError('reserved packet reference key in original input')
            for item in value.values(): count(item)
    count(payload)
    texts = [text for text, n in counts.items() if n > 1]
    index = {text: i for i, text in enumerate(texts)}
    def encode(value):
        if isinstance(value, str) and value in index:
            return {'$text': index[value]}
        if isinstance(value, list): return [encode(item) for item in value]
        if isinstance(value, dict): return {k: encode(v) for k, v in value.items()}
        return value
    packed = dict(packet_format=FORMAT, texts=texts, data=encode(payload))
    # Choose only when actual UTF-8 serialization is smaller; never omit data.
    result = packed if len(compact_json(packed).encode()) < len(compact_json(payload).encode()) else payload
    if unpack(result) != payload:
        raise ValueError('lossless packet round-trip failed')
    return result


def unpack(value):
    if not isinstance(value, dict) or value.get('packet_format') != FORMAT:
        return value
    texts = value['texts']
    def decode(item):
        if isinstance(item, dict) and set(item) == {'$text'}:
            i = item['$text']
            if type(i) is not int or not 0 <= i < len(texts) or not isinstance(texts[i], str):
                raise ValueError('invalid packet text reference')
            return texts[i]
        if isinstance(item, list): return [decode(v) for v in item]
        if isinstance(item, dict): return {k: decode(v) for k, v in item.items()}
        return item
    return decode(value['data'])
