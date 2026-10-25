"""Saved independent Claim/CVP judgement + original-source input certificate.

Compile from the independent reader's exact complete scoped SQL/material inputs,
not whole history, old summary flags or another model. Consumer rechecks current
members/MVCC/source/code/cache bindings but never reconstructs prompts or makes a
judgement. Global layer1/2/non-target/apply/owner/group gates remain REQUIRED.
This module intentionally rejects objections, provider errors and incomplete
audits: existing final-resolution/content-blocked proofs need their own exact
compiled authority, never a changed Gemini pass.
"""
import importlib.util
from pathlib import Path
import hashlib
import random
import sys

from backend.api.canonical_repository.viewpoint_foundation import sha256_json, canonical_json
from backend.pipeline.source_reconciliation_runner import body, seal
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.pipeline.viewpoint_validation_context import ValidationContext, ValidationLimits
from backend.pipeline.viewpoint_saved_cvp_certificate import _bound, _artifact

PROJECT=Path(__file__).resolve().parents[2]
SCHEMA='wang_saved_complete_audit_model_input_certificate_v1'
LIMITS=ValidationLimits(64*1024**2,5)
MAX_CERTIFICATE_BYTES=4*1024**2
CODE=(Path(__file__).resolve(),PROJECT/'scripts/audit-library.py',PROJECT/'scripts/audit-readset.py',
      PROJECT/'scripts/audit-readset-index.sql',
      PROJECT/'config/wang_cvp_contextual_semantics_v2.md',
      PROJECT/'backend/pipeline/viewpoint_saved_cvp_certificate.py',
      PROJECT/'backend/pipeline/viewpoint_resolution_runtime.py',
      PROJECT/'backend/pipeline/viewpoint_validation_context.py',
      PROJECT/'backend/api/canonical_repository/viewpoint_foundation.py',
      PROJECT/'backend/pipeline/source_reconciliation_runner.py')


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);sys.modules[name]=result
    spec.loader.exec_module(result)
    return result


def _raw(ctx,pin):
    return ctx.read(_bound(ctx,pin))


def _binding(ctx,path):
    return {'path':str(Path(path).absolute()),'file_sha256':ctx.pin(path)}


def _reports(audit,claim_ids,viewpoint_ids):
    for layer,field,ids,verdict in (('3','claim_id',claim_ids,'supported'),('4','viewpoint_id',viewpoint_ids,'same')):
        report=audit['layers'][layer];rows=report['results']
        actual=[r[field] for r in rows]
        if (report.get('complete') is not True or sorted(actual)!=sorted(ids) or len(actual)!=len(set(actual))
                or report.get('population')!=len(ids) or report.get('sampled')!=len(ids)
                or report.get('judged')!=len(ids)
                or any(report.get(k,0) for k in ('disputed','blocked','model_errors','no_source_text'))
                or any(r.get('verdict')!=verdict or r.get('paragraphs_dropped_to_pass_filter',0) for r in rows)):
            raise ValueError('only exact complete unshortened supported/same saved audit accepted; no verdict override')


def compile_saved_audit(*,audit_binding,claim_ids,viewpoint_ids,database_url,data_directory,cache_directory,output):
    reader=module('wkp357_saved_audit_reader',PROJECT/'scripts/audit-library.py')
    scope=module('wkp357_independent_audit_readset',PROJECT/'scripts/audit-readset.py')
    claim_ids,viewpoint_ids=scope.identifiers(claim_ids),scope.identifiers(viewpoint_ids)
    if not Path(output).is_absolute() or not Path(data_directory).is_absolute() or not Path(cache_directory).is_absolute():
        raise ValueError('explicit absolute audit data/cache/output paths required')
    declaration={'audit':audit_binding,'claim_ids':claim_ids,'viewpoint_ids':viewpoint_ids,
                 'data_directory':str(data_directory),'cache_directory':str(cache_directory)}
    with ValidationContext('compile saved independent audit',limits=LIMITS) as ctx:
        ctx.depth=1;ctx.receipt_policy_stack.append(False)
        for path in CODE:ctx.pin(path)
        audit=_raw(ctx,audit_binding);_reports(audit,claim_ids,viewpoint_ids)
        if (audit['meta']['model']!='gemini-3.8-flash'
                or audit['meta']['semantic_contract_version']!=reader.CONTEXTUAL_SEMANTICS
                or audit['meta']['semantic_contract_file_sha256']!=ctx.pin(reader.SEMANTIC_RULES_PATH)):
            raise ValueError('actual independent model/semantic contract drift')
        frame=scope.AuditReadset(database_url,claim_ids,viewpoint_ids)
        sources=reader.SourceIndex(frame.collection('source_documents'),Path(data_directory))
        source_bindings={}
        for sid,document in sources.documents.items():
            path=sources.path_for(document)
            if path is None:raise ValueError('complete own original missing')
            if not document.get('source_path') or Path(document['source_path'])!=path or not path.is_absolute():
                raise ValueError('compiled source requires exact explicit original path; no implicit fallback')
            ctx.pin(path)
            source_bindings[sid]=_binding(ctx,path)
            source=sources.file_for(sid)
            if source is None or source.sha256!=ctx.files[Path(path).absolute()][1]:
                raise ValueError('physical source changed while independently reading')
            declared=document.get('source_body_sha256') if source.body_coordinates else document.get('source_sha256')
            if not declared or declared!=source.current_sha256:
                raise ValueError('physical source/body version differs from current source document')
        captured=[]
        class SavedOnly:
            preserve_full_context=True
            def judge(self,prompt,schema):
                digest=hashlib.sha256(('gemini-3.8-flash\n'+prompt).encode()).hexdigest()
                path=Path(cache_directory)/(digest+'.json')
                response=_raw(ctx,_binding(ctx,path))
                if response.get('verdict') not in {'supported','same'}:
                    raise ValueError('saved provider judgement not complete pass; no fallback/arbitration')
                captured.append({'cache':_binding(ctx,path),'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                                 'schema_sha256':sha256_json(schema),'response_sha256':sha256_json(response)})
                return dict(response)
        for layer,field,ids,fn in (('3','claim_id',claim_ids,reader.audit_claims),
                                  ('4','viewpoint_id',viewpoint_ids,reader.audit_viewpoints)):
            start=len(captured)
            result=fn(frame,sources,SavedOnly(),None,random.Random(0),workers=1,only=set(ids))
            if sorted(result['results'],key=lambda r:r[field])!=sorted(audit['layers'][layer]['results'],key=lambda r:r[field]):
                raise ValueError('actual scoped complete original prompt/cache/result does not reproduce saved audit')
            if len(captured)-start!=len(result['results']):raise ValueError('one saved request per audit target required')
            for request,row in zip(captured[start:],result['results']):request.update(layer=layer,target_id=row[field])
        if len(captured)!=len(claim_ids)+len(viewpoint_ids):
            raise ValueError('independent audit original request denominator incomplete')
        closing_metrics=frame.assert_unchanged()
        value=dict(schema_version=SCHEMA,status='saved_model_source_inputs_verified_not_group_admission',
            declaration=declaration,declaration_sha256=sha256_json(declaration),current_state=frame.state(),
            current_state_sha256=sha256_json(frame.state()),requests=captured,
            source_bindings=source_bindings,runtime_identity={'python':sys.version.split()[0]},
            dependencies=[{'path':str(p),'file_sha256':pin} for p,(_,pin) in sorted(ctx.files.items())],
            scoped_model_layers_complete=True,global_mechanical_layers_verified=False,sql_admission_granted=False,
            provider_calls=0,registry_writes=0)
    if len(canonical_json(value).encode())>MAX_CERTIFICATE_BYTES:raise ValueError('audit certificate oversized')
    write_immutable(Path(output),seal(value),require_new=True)
    return value,{'files':ctx.metrics,'initial_sql':frame.metrics,'closing_sql':closing_metrics}


def consume_saved_audit(certificate_binding,*,declaration,database_url):
    scope=module('wkp357_independent_audit_readset_consumer',PROJECT/'scripts/audit-readset.py')
    with ValidationContext('consume saved independent audit',limits=LIMITS) as ctx:
        path=Path(certificate_binding['path'])
        if path.stat().st_size>MAX_CERTIFICATE_BYTES:raise ValueError('audit certificate oversized')
        value=_artifact(ctx,certificate_binding)
        if (value.get('schema_version')!=SCHEMA
                or value.get('status')!='saved_model_source_inputs_verified_not_group_admission'
                or value.get('declaration')!=declaration or value.get('declaration_sha256')!=sha256_json(declaration)
                or value.get('current_state_sha256')!=sha256_json(value['current_state'])
                or value.get('global_mechanical_layers_verified') is not False
                or value.get('sql_admission_granted') is not False
                or value.get('scoped_model_layers_complete') is not True
                or value.get('provider_calls')!=0 or value.get('registry_writes')!=0):
            raise ValueError('audit input certificate identity/authority drift')
        if value.get('runtime_identity')!={'python':sys.version.split()[0]}:raise ValueError('audit reader runtime drift')
        pins=value['dependencies'];names=[p['path'] for p in pins]
        requests=value['requests']
        actual=[(r.get('layer'),r.get('target_id')) for r in requests]
        expected=[('3',cid) for cid in declaration['claim_ids']]+[('4',vid) for vid in declaration['viewpoint_ids']]
        if sorted(actual)!=sorted(expected) or len(actual)!=len(set(actual)):raise ValueError('audit target/request denominator drift')
        documents=value['current_state']['rows']['source_documents'];sources=value['source_bindings']
        if set(sources)!={sid for sid,row in documents.items() if not row['retired']}:
            raise ValueError('audit physical original source cut incomplete')
        for sid,pin in sources.items():
            if pin['path']!=documents[sid]['payload'].get('source_path'):
                raise ValueError('audit source path identity drift')
        required={str(p) for p in CODE}|{declaration['audit']['path']}|{r['cache']['path'] for r in requests}|{p['path'] for p in sources.values()}
        if len(names)!=len(set(names)) or not required<=set(names):raise ValueError('audit proof dependency cut incomplete')
        by_path={p['path']:p for p in pins}
        for pin in [declaration['audit'],*sources.values(),*(r['cache'] for r in requests)]:
            if by_path.get(pin['path'])!=pin:raise ValueError('audit proof declared physical binding differs from read dependency')
        for pin in pins:_bound(ctx,pin)
        frame=scope.AuditReadset(database_url,declaration['claim_ids'],declaration['viewpoint_ids'])
        if frame.state()!=value['current_state']:raise ValueError('current complete independent audit readset drift')
        closing=frame.assert_unchanged()
    return value,{'files':ctx.metrics,'initial_sql':frame.metrics,'closing_sql':closing}
