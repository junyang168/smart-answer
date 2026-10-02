"""Lossless, model-readable packet interning. No summaries or context selection.

Long repeated strings are stored once. A {"$text": index} value resolves to
texts[index]. All original keys, values, list order and source text survive.
"""
from __future__ import annotations
from collections import Counter
import json

FORMAT = 'wang_exegesis_interned_packet_v1'
OBJECT_FORMAT = 'wang_exegesis_interned_objects_v2'
INSTRUCTION = ('wang_exegesis_interned_objects_v2 另含 object_keys 字段名表；'
               '仅含 \"$o\" 的对象，其数组首项是 object_keys 的索引，其后各项依序对应字段值。'
               '递归还原对象与 $text 引用后读取；数据无摘要、无删减。\n'
               '输入可能是 wang_exegesis_interned_packet_v1 无损 packet：texts 是字符串表，'
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
            if '$text' in value or '$o' in value:
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
    # Field names repeated thousands of times are another lossless redundancy.
    short_counts = Counter()
    shapes = []
    def inspect(value):
        if isinstance(value, str) and len(value.encode()) >= 32:
            short_counts[value] += 1
        elif isinstance(value, list):
            for item in value: inspect(item)
        elif isinstance(value, dict):
            keys = list(value)
            if keys not in shapes: shapes.append(keys)
            for item in value.values(): inspect(item)
    inspect(payload)
    object_texts = [text for text, n in short_counts.items() if n > 1]
    object_index = {text: i for i, text in enumerate(object_texts)}
    def encode_objects(value):
        if isinstance(value, str) and value in object_index:
            return {'$text': object_index[value]}
        if isinstance(value, list): return [encode_objects(item) for item in value]
        if isinstance(value, dict):
            return {'$o': [shapes.index(list(value)), *[encode_objects(item) for item in value.values()]]}
        return value
    objects = dict(packet_format=OBJECT_FORMAT, texts=object_texts, object_keys=shapes, data=encode_objects(payload))
    result = min((payload, packed, objects), key=lambda value: len(compact_json(value).encode()))
    if unpack(result) != payload:
        raise ValueError('lossless packet round-trip failed')
    return result


def unpack(value):
    if not isinstance(value, dict) or value.get('packet_format') not in {FORMAT, OBJECT_FORMAT}:
        return value
    texts = value['texts']
    shapes = value.get('object_keys', [])
    def decode(item):
        if isinstance(item, dict) and set(item) == {'$o'}:
            fields = item['$o']
            if not isinstance(fields, list) or not fields or type(fields[0]) is not int or not 0 <= fields[0] < len(shapes):
                raise ValueError('invalid packet object reference')
            keys = shapes[fields[0]]
            if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys) or len(keys) != len(set(keys)) or len(keys) != len(fields) - 1:
                raise ValueError('invalid packet object fields')
            return {key: decode(item) for key, item in zip(keys, fields[1:])}
        if isinstance(item, dict) and set(item) == {'$text'}:
            i = item['$text']
            if type(i) is not int or not 0 <= i < len(texts) or not isinstance(texts[i], str):
                raise ValueError('invalid packet text reference')
            return texts[i]
        if isinstance(item, list): return [decode(v) for v in item]
        if isinstance(item, dict): return {k: decode(v) for k, v in item.items()}
        return item
    return decode(value['data'])
