"""Materialized historical readsets; NEVER semantic or SQL authorization.

An explicit, budgeted migration streams an already-completed snapshot once,
authenticates its physical SHA, and materializes only declared rows/absences.
Consumers authenticate the small projection, code and declaration, not the old
whole snapshot. The archived snapshot SHA remains provenance. Fresh source,
model, hold, applied-SQL and non-target integrity gates are separate and MUST
still be implemented before this primitive can be a production admission path.

No automatic compiler fallback, database connection, model or queue exists.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import pydantic

from backend.pipeline import source_reconciliation as c
from backend.pipeline.source_reconciliation_runner import body, read, seal
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.pipeline.viewpoint_validation_context import (
    ValidationContext, ValidationLimits, ROUTINE_VALIDATION_LIMITS, active_context,
    require_explicit_history_maintenance,
)

SCHEMA = 'wang_compiled_historical_readset_projection_v1'
PREDECESSOR_SCHEMA = 'wang_flat_predecessor_projection_v1'
MAX_PROJECTION_BYTES = 8*1024**2
_PROJECT = Path(__file__).resolve().parents[2]
CODE = (Path(__file__), Path(c.__file__), Path(__file__).with_name('source_reconciliation_runner.py'),
        *(_PROJECT/name for name in (
            'backend/api/canonical_repository/knowledge_models.py',
            'backend/api/canonical_repository/viewpoint_foundation.py',
            'backend/api/canonical_repository/viewpoint_production_safety.py',
            'backend/pipeline/viewpoint_resolution_runtime.py',
            'backend/pipeline/viewpoint_completed_predecessors.py',
            'backend/pipeline/viewpoint_applied_source_epochs.py')))


def runtime_identity():
    return {'python':sys.version.split()[0],'pydantic':pydantic.__version__}


@dataclass(frozen=True)
class CompileLimits:
    bytes: int = 512*1024**2
    seconds: float = 30
    value_bytes: int = 8*1024**2

    def __post_init__(self):
        if (type(self.bytes) is not int or self.bytes <= 0 or type(self.value_bytes) is not int
                or self.value_bytes <= 0 or type(self.seconds) not in (int,float)
                or not 0 < self.seconds < float('inf')):
            raise ValueError('positive finite compilation budgets required')


class _Stream:
    """Bounded UTF-8/JSON streaming for a snapshot object or shared row array."""
    specials = re.compile(r'[{}\[\]"\\]')

    def __init__(self, path, limits, metrics, started, *, chunk_bytes=64*1024):
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 1024**2:
            raise ValueError('bounded positive streaming chunk required')
        self.path, self.limits, self.metrics, self.started = path, limits, metrics, started
        self.chunk_bytes = chunk_bytes
        self.handle = path.open('rb')
        self.utf8 = codecs.getincrementaldecoder('utf-8')()
        self.buffer, self.pos, self.eof = '', 0, False
        self.digest = hashlib.sha256()
        self.decoder = json.JSONDecoder()

    def check(self):
        if time.perf_counter()-self.started > self.limits.seconds:
            raise ValueError('explicit projection compilation time budget exceeded; no fallback')

    def more(self):
        self.check()
        self.buffer = self.buffer[self.pos:]; self.pos = 0
        raw = self.handle.read(self.chunk_bytes)
        self.metrics['input_bytes'] += len(raw)
        if self.metrics['input_bytes'] > self.limits.bytes:
            raise ValueError('explicit projection compilation IO budget exceeded; no fallback')
        self.digest.update(raw)
        if not raw:
            self.eof = True
        self.buffer += self.utf8.decode(raw, final=self.eof)
        return bool(raw)

    def peek(self):
        while self.pos == len(self.buffer) and not self.eof:
            self.more()
        return self.buffer[self.pos] if self.pos < len(self.buffer) else ''

    def whitespace(self):
        while self.peek() in (' ', '\t', '\r', '\n'):
            self.pos += 1

    def expect(self, token):
        self.whitespace()
        if self.peek() != token:
            raise ValueError('invalid snapshot JSON delimiter: expected '+token)
        self.pos += 1

    def value(self):
        self.whitespace()
        while True:
            self.check()
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.pos)
                # A number can end at a buffer boundary before the next digit.
                if end == len(self.buffer) and not self.eof:
                    self.more(); continue
                if len(self.buffer[self.pos:end].encode('utf-8')) > self.limits.value_bytes:
                    raise ValueError('snapshot individual value exceeds compilation budget')
                self.pos = end
                self.metrics['decoded_values'] += 1
                return value
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError('incomplete/invalid snapshot JSON value') from None
                if len(self.buffer[self.pos:].encode('utf-8')) >= self.limits.value_bytes:
                    raise ValueError('snapshot individual value exceeds compilation budget')
                self.more()

    def skip(self):
        """Skip an unselected container without materializing its JSON tree.

        Input is independently content-bound to an existing completed artifact;
        this is an extractor, not a replacement for its original schema audit.
        All selected rows are parsed/validated, and the entire physical input
        still must match the completed artifact's SHA before publication.
        """
        self.whitespace()
        if self.peek() not in ('{','['):
            self.value(); return
        stack = ['}' if self.peek() == '{' else ']']; self.pos += 1
        quoted, escaped = False, False
        while stack:
            self.check()
            if not self.peek():
                raise ValueError('incomplete unselected snapshot container')
            if escaped:
                self.pos += 1; escaped = False; continue
            match = self.specials.search(self.buffer, self.pos)
            if match is None:
                self.pos = len(self.buffer); continue
            char = match.group(); self.pos = match.end()
            if quoted:
                if char == '\\': escaped = True
                elif char == '"': quoted = False
            elif char == '"': quoted = True
            elif char in ('{','['):
                stack.append('}' if char == '{' else ']')
                if len(stack) > 256:raise ValueError('snapshot nesting exceeds compilation budget')
            elif char in ('}',']'):
                if stack.pop() != char:raise ValueError('unselected snapshot JSON nesting mismatch')
        if quoted:raise ValueError('unclosed snapshot string')

    def rows(self, wanted, found):
        self.expect('['); self.whitespace()
        if self.peek() == ']':self.pos += 1;return
        while True:
            row = c.Row.model_validate(self.value())
            self.metrics['snapshot_rows_scanned'] += 1
            if row.key in wanted:
                if row.key in found:raise ValueError('duplicate selected historical row')
                # c.index retains the exact original payload identity guard.
                c.index([row]); encoded = row.model_dump(mode='json')
                self.metrics['selected_bytes'] += len(json.dumps(encoded,ensure_ascii=False).encode('utf-8'))
                if self.metrics['selected_bytes'] > MAX_PROJECTION_BYTES:
                    raise ValueError('selected readset exceeds routine projection budget')
                found[row.key] = encoded
            self.whitespace()
            delimiter = self.peek(); self.pos += 1
            if delimiter == ']':return
            if delimiter != ',':raise ValueError('invalid snapshot row-array delimiter')

    def finish(self, expected_sha):
        self.whitespace()
        if self.peek():raise ValueError('trailing snapshot JSON data')
        if self.digest.hexdigest() != expected_sha:
            raise ValueError('completed snapshot physical SHA drift')
        self.check()

    def close(self):self.handle.close()


def _keys(keys):
    if not isinstance(keys, list) or not keys or any(not isinstance(k,str) for k in keys) or len(set(keys)) != len(keys):
        raise ValueError('nonempty unique explicit readset required')
    for k in keys:c.split_key(k)
    return sorted(keys)


def _small(bound):
    path = Path(bound['path'])
    if path.stat().st_size > MAX_PROJECTION_BYTES:
        raise ValueError('bounded projection input exceeds maximum size')
    c.verify_file(bound)
    return read(path)


def compile_projection(completion_binding, snapshot_path, keys, output, *, field='records',
                       sections=(), limits=CompileLimits(), chunk_bytes=64*1024):
    """Explicit migration only. Complete SHA match required before publishing.

    The completion is an upstream physical anchor, not semantic approval newly
    granted by this function. Its other bindings/actual database gates remain
    the responsibility of the eventual compiled admission certificate builder.
    """
    output, snapshot_path = Path(output), Path(snapshot_path)
    if not output.is_absolute() or not snapshot_path.is_absolute():
        raise ValueError('absolute projection paths required')
    if output.exists():raise ValueError('projection already exists; no overwrite/recompile fallback')
    keys = _keys(keys); wanted = set(keys)
    completion = _small(completion_binding)
    if completion.get('status') not in ('complete','audited_complete'):
        raise ValueError('snapshot is not anchored to a completed artifact')
    pins = [b for b in completion.get('bindings',[]) if b.get('path') == str(snapshot_path)]
    if not pins or any(b != pins[0] for b in pins):
        raise ValueError('snapshot has no exact unambiguous completed physical binding')
    snapshot_binding = pins[0]
    if (not isinstance(field,str) or not field or not isinstance(sections,(tuple,list))
            or len(set(sections)) != len(sections) or field in sections
            or any(not isinstance(s,str) for s in sections)):
        raise ValueError('invalid projection section denominator')
    metrics = dict(input_bytes=0,decoded_values=0,snapshot_rows_scanned=0,selected_bytes=0)
    started = time.perf_counter(); found, headers, fields = {}, {}, set()
    stream = _Stream(snapshot_path,limits,metrics,started,chunk_bytes=chunk_bytes)
    shared, has_shared = None, False
    try:
        stream.expect('{')
        while True:
            stream.whitespace()
            if stream.peek() == '}':stream.pos += 1;break
            name = stream.value()
            if not isinstance(name,str) or name in fields:raise ValueError('duplicate/invalid snapshot root field')
            fields.add(name);stream.expect(':');stream.whitespace()
            if name == field:
                if stream.peek() == '[':stream.rows(wanted,found)
                else:shared = stream.value();has_shared = True
            elif name in sections:headers[name] = stream.value()
            else:stream.skip()
            stream.whitespace(); delimiter = stream.peek(); stream.pos += 1
            if delimiter == '}':break
            if delimiter != ',':raise ValueError('invalid snapshot root delimiter')
        stream.finish(snapshot_binding['file_sha256'])
    finally:stream.close()
    if field not in fields or set(headers) != set(sections):raise ValueError('projection fields missing')
    if has_shared:
        if (not isinstance(shared,dict) or set(shared) != {'wkp_shared_records_v1','size'}
                or type(shared['size']) is not int or shared['size'] < 0):
            raise ValueError('snapshot field is not rows or exact shared row reference')
        digest = shared['wkp_shared_records_v1']
        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('invalid shared records SHA')
        if 'source-reconciliation' not in snapshot_path.parts:raise ValueError('shared rows outside owning archive')
        base = Path(*snapshot_path.parts[:snapshot_path.parts.index('source-reconciliation')+1])
        blob = base/'immutable-snapshots'/(digest+'.rows.json')
        if blob.stat().st_size != shared['size']:raise ValueError('shared snapshot size drift')
        stream = _Stream(blob,limits,metrics,started,chunk_bytes=chunk_bytes)
        try:stream.rows(wanted,found);stream.finish(digest)
        finally:stream.close()
    c.index(list(found.values()))
    projection = dict(schema_version=SCHEMA,approval_status='projection_only_not_admission',
        completion=completion_binding,snapshot=snapshot_binding,field=field,
        readset_keys=keys,readset_sha256=c.sha256_json(keys),
        sections=headers,section_names=sorted(sections),
        rows=[found[k] for k in sorted(found)],absent_keys=sorted(wanted-found.keys()),
        compiler_bindings=[{'path':str(p.resolve()),'file_sha256':c.file_sha256(p)} for p in CODE],
        compiler_runtime=runtime_identity(),
        semantic_approval_granted=False,registry_writes=0,model_calls=0)
    encoded = json.dumps(seal(projection),ensure_ascii=False,indent=2).encode('utf-8')
    if len(encoded)+1 > MAX_PROJECTION_BYTES:raise ValueError('projection exceeds routine size ceiling')
    # Reauthenticate the SMALL upstream anchor and code after compilation. The
    # large physical input was hashed while streaming, not reread/reparsed.
    c.verify_file(completion_binding)
    for pin in projection['compiler_bindings']:c.verify_file(pin)
    write_immutable(output,seal(projection),require_new=True)
    return {'projection':{'path':str(output),'file_sha256':c.file_sha256(output)},
        'status':'projection_compiled_not_admission','output_bytes':len(encoded)+1,
        'wall_seconds':time.perf_counter()-started,**metrics,
        'model_calls':0,'registry_writes':0,'production_resume_authorized':False}


def consume_projection(bound, keys, *, completion_binding, snapshot_binding, field='records', sections=()):
    """Read only the declared projection. A miss/drift never opens old history."""
    keys = _keys(keys)
    if not isinstance(field,str) or not isinstance(sections,(tuple,list)) or len(set(sections)) != len(sections):
        raise ValueError('explicit projection field/section denominator required')
    if active_context() is None:
        with ValidationContext('compiled historical readset',limits=ROUTINE_VALIDATION_LIMITS):
            return consume_projection(bound,keys,completion_binding=completion_binding,
                                      snapshot_binding=snapshot_binding,field=field,sections=sections)
    value = body(_small(bound))
    if (value.get('schema_version') != SCHEMA or value.get('approval_status') != 'projection_only_not_admission'
            or value.get('semantic_approval_granted') is not False
            or value.get('model_calls') != 0 or value.get('registry_writes') != 0):
        raise ValueError('projection cannot assert admission or semantic approval')
    if (value.get('completion') != completion_binding or value.get('snapshot') != snapshot_binding
            or value.get('field') != field or value.get('readset_keys') != keys
            or value.get('readset_sha256') != c.sha256_json(keys)
            or value.get('section_names') != sorted(sections) or set(value.get('sections',{})) != set(sections)):
        raise ValueError('compiled readset/source/section denominator drift')
    code_pins = value.get('compiler_bindings',[])
    if len(code_pins) != len(CODE) or {p.get('path') for p in code_pins} != {str(p.resolve()) for p in CODE}:
        raise ValueError('compiled readset compiler denominator drift')
    for pin in code_pins:c.verify_file(pin)
    if value.get('compiler_runtime') != runtime_identity():
        raise ValueError('compiled readset runtime identity drift')
    # Upstream completion/snapshot pins are archived provenance of this copied
    # projection, NOT live admission inputs. Do not traverse H historical
    # completions here. The consumer must supply its declared exact anchor;
    # this function grants NO approval. Current model/source/hold/SQL/global
    # integrity must be established by a separate admission certificate guard.
    rows = c.index(value.get('rows',[])); absent = value.get('absent_keys',[])
    if (len(set(absent)) != len(absent) or set(absent)&rows.keys()
            or rows.keys()|set(absent) != set(keys)):
        raise ValueError('compiled positive/negative readset population drift')
    return {'rows':rows,'absent_keys':absent,'sections':value['sections'],
            'approval_status':'projection_only_not_admission'}


def _projected_state(spec, keys):
    if not isinstance(spec,dict) or set(spec) != {'projection','completion','snapshot','field'}:
        raise ValueError('exact historical projection declaration required')
    value = consume_projection(spec['projection'],keys,
        completion_binding=spec['completion'],snapshot_binding=spec['snapshot'],field=spec['field'])
    return {k:value['rows'].get(k) for k in keys}


def compile_predecessor_map(transitions, keys, output, *, limits=CompileLimits()):
    """Explicitly flatten declared, projected transitions ONCE, never approval.

    Each transition supplies exact before/after projection bindings. All rows
    and negative identities have the same denominator; adjacent states must be
    byte-for-byte equal after JSON decoding. The output has only the oldest and
    latest D rows, not an H-long history list. Its chain digest is provenance,
    not proof that any transition had a valid review or SQL application.

    A future admission consumer MUST establish those separate authorities and
    full non-target integrity before using this map. Routine callers cannot
    compile on a miss or silently fall back to recursive epoch reconstruction.
    """
    require_explicit_history_maintenance('compile flat historical predecessor map')
    keys, output = _keys(keys), Path(output)
    if not output.is_absolute() or output.exists():
        raise ValueError('new absolute predecessor projection path required')
    if not isinstance(transitions,list) or not transitions:
        raise ValueError('nonempty explicit chronological transition chain required')
    started=time.perf_counter(); read_bytes=0; previous=None; oldest=None
    seen=set(); seen_states=set(); pins=[]
    code_pins=[{'path':str(p.resolve()),'file_sha256':c.file_sha256(p)} for p in CODE]
    with ValidationContext('explicit predecessor compilation',
                           limits=ValidationLimits(limits.bytes,limits.seconds)) as ctx:
        for transition in transitions:
            if not isinstance(transition,dict) or set(transition) != {'before','after'}:
                raise ValueError('exact before/after transition declaration required')
            identity=c.sha256_json(transition)
            if identity in seen:raise ValueError('duplicate historical transition/cycle')
            seen.add(identity)
            for spec in transition.values():
                if not isinstance(spec,dict) or 'projection' not in spec:
                    raise ValueError('historical projection binding missing')
                pin=spec['projection']; size=Path(pin['path']).stat().st_size
                read_bytes += size
                if read_bytes > limits.bytes or size > limits.value_bytes:
                    raise ValueError('explicit predecessor compilation IO/value budget exceeded; no fallback')
                pins.append(pin)
            before=_projected_state(transition['before'],keys)
            after=_projected_state(transition['after'],keys)
            if previous is not None and previous != before:
                raise ValueError('historical predecessor continuity gap/order drift')
            if oldest is None:
                oldest=before; seen_states.add(c.sha256_json(before))
            if before != after:
                digest=c.sha256_json(after)
                if digest in seen_states:raise ValueError('historical state cycle')
                seen_states.add(digest)
            previous=after
            if time.perf_counter()-started > limits.seconds:
                raise ValueError('explicit predecessor compilation time budget exceeded; no fallback')
        # Reauthenticate all small inputs before publishing. They are copied
        # provenance after publication, not traversed by the routine consumer.
        for pin in pins:c.verify_file(pin)
    value=dict(schema_version=PREDECESSOR_SCHEMA,
        approval_status='projection_only_not_admission',semantic_approval_granted=False,
        readset_keys=keys,chain_sha256=c.sha256_json(transitions),
        first_before=transitions[0]['before'],last_after=transitions[-1]['after'],
        oldest=oldest,latest=previous,
        compiler_bindings=code_pins,
        compiler_runtime=runtime_identity(),model_calls=0,registry_writes=0)
    if len(json.dumps(seal(value),ensure_ascii=False).encode('utf-8')) > MAX_PROJECTION_BYTES:
        raise ValueError('flat predecessor projection exceeds routine size ceiling')
    if time.perf_counter()-started > limits.seconds:
        raise ValueError('explicit predecessor compilation time budget exceeded; no fallback')
    for pin in code_pins:c.verify_file(pin)
    write_immutable(output,seal(value),require_new=True)
    return dict(projection={'path':str(output),'file_sha256':c.file_sha256(output)},
        chain_sha256=value['chain_sha256'],transition_count=len(transitions),
        selected_keys=len(keys),input_projection_bytes=read_bytes,
        authenticated_file_io_bytes=ctx.metrics['file_io_bytes'],
        wall_seconds=time.perf_counter()-started,model_calls=0,registry_writes=0,
        production_resume_authorized=False)


def consume_predecessor_map(bound, keys, states, *, chain_sha256, first_before, last_after):
    """Restore only exact D historical identities; never open H old artifacts.

    `states` is the explicitly authenticated latest scoped map, not a whole
    Registry snapshot. None denotes a declared absence (including creation in
    an earlier transition). No database mutation or semantic authority results.
    """
    keys=_keys(keys)
    if not isinstance(states,dict) or set(states) != set(keys):
        raise ValueError('exact latest historical readset required; no whole Registry fallback')
    if active_context() is None:
        with ValidationContext('flat predecessor consumption',limits=ROUTINE_VALIDATION_LIMITS):
            return consume_predecessor_map(bound,keys,states,chain_sha256=chain_sha256,
                first_before=first_before,last_after=last_after)
    value=body(_small(bound))
    if (value.get('schema_version') != PREDECESSOR_SCHEMA
            or value.get('approval_status') != 'projection_only_not_admission'
            or value.get('semantic_approval_granted') is not False
            or value.get('model_calls') != 0 or value.get('registry_writes') != 0):
        raise ValueError('historical projection cannot grant admission/approval')
    if (value.get('readset_keys') != keys or value.get('chain_sha256') != chain_sha256
            or value.get('first_before') != first_before or value.get('last_after') != last_after
            or value.get('compiler_runtime') != runtime_identity()):
        raise ValueError('flat predecessor identity/readset/chain/runtime drift')
    pins=value.get('compiler_bindings',[])
    if len(pins) != len(CODE) or {p.get('path') for p in pins} != {str(p.resolve()) for p in CODE}:
        raise ValueError('flat predecessor compiler denominator drift')
    for pin in pins:c.verify_file(pin)
    for label in ('oldest','latest'):
        rows=value.get(label)
        if not isinstance(rows,dict) or set(rows) != set(keys):
            raise ValueError('flat predecessor positive/negative population drift')
        c.index([dict(collection=c.split_key(k)[0],object_id=c.split_key(k)[1],payload=v)
                 for k,v in rows.items() if v is not None])
    if value['latest'] != states:
        raise ValueError('latest historical postimage/version/absence drift')
    return {'rows':value['oldest'],'approval_status':'projection_only_not_admission'}
