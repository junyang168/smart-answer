"""Read one slide-model answer without changing the contents of any result row."""
import json
import re


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"Duplicate slide response field: {key}")
        obj[key] = value
    return obj


_decoder = json.JSONDecoder(object_pairs_hook=_unique_object)


def parse_slide_answer(text, schema=None, *, id_field=None, expected_ids=None):
    text = text.strip()
    try:
        value, end = _decoder.raw_decode(text)
    except json.JSONDecodeError:
        if expected_ids is None:
            raise
        value = _complete_rows(text, id_field, expected_ids)
    else:
        tail = text[end:].strip()
        if schema and value == schema:
            if re.match(r'^,\s*"results"\s*:', tail):
                tail = "{" + tail[1:]
            # A second schema, an extra verdict or an empty answer is invalid.
            return _parse_payload(tail, id_field, expected_ids)
        if tail:
            raise ValueError("Unexpected extra slide response data")
        if schema and isinstance(value, dict) and "results" in value:
            extras = value.keys() - {"results"}
            if extras:
                if any(k not in schema or value[k] != schema[k] for k in extras):
                    raise ValueError("Unexpected fields alongside slide results")
                value = {"results": value["results"]}
    return value


def _parse_payload(text, id_field, expected_ids):
    value = parse_slide_answer(text, id_field=id_field, expected_ids=expected_ids)
    if not isinstance(value, dict) or set(value) != {"results"}:
        raise ValueError("Expected one results object after slide schema")
    return value


def _complete_rows(text, id_field, expected_ids):
    """Recover a damaged closing delimiter only after all requested rows are intact."""
    prefix = re.match(r'\s*\{\s*"results"\s*:\s*\[', text)
    if not prefix or not expected_ids:
        raise ValueError("Missing slide result rows")
    tail = text[prefix.end():].lstrip()
    rows = []
    for index, expected in enumerate(expected_ids):
        if index:
            if not tail.startswith(","):
                raise ValueError("Missing slide result row")
            tail = tail[1:].lstrip()
        row, end = _decoder.raw_decode(tail)
        if not isinstance(row, dict) or row.get(id_field) != expected:
            raise ValueError("Unexpected slide result ID")
        rows.append(row)
        tail = tail[end:].lstrip()
    if not re.fullmatch(r'\]?\s*(?:[\w\s。]*"?|procedure\s*:\s*"none")\s*\]?\s*\}', tail):
        raise ValueError("Ambiguous slide response after complete rows")
    return {"results": rows}
