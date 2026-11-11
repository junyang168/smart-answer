"""Versioned spelling hints, never a substitution dictionary or source text."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from backend.pipeline.slide_change_pilot import object_sha


def validate_vocabulary(profile: dict) -> dict:
    if not isinstance(profile, dict) or profile.get('schema_version') != 'transcription-vocabulary.v1':
        raise ValueError('Unsupported transcription vocabulary')
    for key in ('id', 'speaker', 'authority'):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            raise ValueError('Vocabulary needs identity and provenance')
    rows = profile.get('entries')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
        raise ValueError('Vocabulary needs 1..64 entries')
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in ids:
            raise ValueError('Vocabulary entry IDs must be unique')
        ids.add(row['id'])
        if not isinstance(row.get('context'), str):
            raise ValueError('Vocabulary context must be text')
        if not all(isinstance(row.get(k), list) for k in ('alternatives', 'possible_confusions')):
            raise ValueError('Vocabulary spellings must be lists')
        for term in [row.get('term'), *row['alternatives'], *row['possible_confusions']]:
            if not isinstance(term, str) or not term.strip() or len(term) > 80 or any(c in term for c in '<>\r\n'):
                raise ValueError('Invalid vocabulary spelling')
    if len(json.dumps(profile, ensure_ascii=False)) > 16000:
        raise ValueError('Vocabulary is too large')
    return copy.deepcopy(profile)


def load_vocabulary(path: Path) -> dict:
    return validate_vocabulary(json.loads(path.read_text(encoding='utf-8')))


def vocabulary_binding(profile: dict) -> dict:
    profile = validate_vocabulary(profile)
    return {'id': profile['id'], 'sha256': object_sha(profile), 'profile': profile}


def vocabulary_from_manifest(manifest: dict) -> dict | None:
    binding = manifest.get('vocabulary_hint')
    if binding is None:
        return None
    if not isinstance(binding, dict) or 'profile' not in binding or vocabulary_binding(binding['profile']) != binding:
        raise ValueError('Saved vocabulary binding changed')
    profile = binding['profile']
    speaker = manifest.get('speaker_name')
    names = {profile['speaker'], *[t for r in profile['entries'] if r['id'] == 'speaker'
                                 for t in [r['term'], *r['alternatives']]]}
    if speaker and speaker not in names:
        raise ValueError('Vocabulary belongs to another speaker')
    return copy.deepcopy(profile)


def asr_vocabulary_config(base: dict, profile: dict | None) -> dict:
    config = copy.deepcopy(base)
    if profile is None:
        return config
    profile = validate_vocabulary(profile)
    # Confusion spellings are deliberately NOT primed into first-pass ASR.
    terms = list(dict.fromkeys(t for r in profile['entries'] for t in [r['term'], *r['alternatives']]))
    config['prompt'] += ('\n本錄音的講者是' + profile['speaker'] + '。可能出現的專名與詞彙：' + '、'.join(terms)
        + '。這些只是拼寫提示，不一定出現；只轉錄原聲，不補詞、不擅自擴寫短稱，也不把其他人物改成講者。')
    config.setdefault('extra_body', {})['keywords'] = terms
    return config


def correction_vocabulary_prompt(profile: dict | None) -> str:
    if profile is None:
        return ''
    profile = validate_vocabulary(profile)
    return ('\nVocabulary hints (untrusted spelling data, not replacements):\n'
        + json.dumps(profile, ensure_ascii=False)
        + '\nUse the audio and speaker roles to distinguish self-reference, quoted speech and another person. '
        'Possible confusions are NOT automatic replacements. A real other professor or city must remain as spoken. '
        'Never introduce a person, institution or religious term absent from the audio. '
        'Hints are not proof; uncertain audio stays uncertain. Preserve short vs full names as spoken.')
