"""Independent reader consumes exact unchanged pending facts, not producer prose."""
from pathlib import Path
import importlib.util
import sys
import pytest
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store
from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha

spec=importlib.util.spec_from_file_location('independent_pending_scope',Path(__file__).resolve().parents[2]/'scripts/audit-library.py')
reader=importlib.util.module_from_spec(spec);sys.modules[spec.name]=reader;spec.loader.exec_module(reader)


@pytest.mark.parametrize('keys',[['evidence_steps/E'],['no-slash'],['canonical_viewpoints/'],
    ['canonical_viewpoints/V']*2,['canonical_viewpoints/'+str(i) for i in range(129)]])
def test_bad_pending_population_stops_before_sql(keys,monkeypatch):
    monkeypatch.setattr(reader.SqlGroupStore,'_query',lambda *a:pytest.fail('invalid scope must not reach SQL'))
    with pytest.raises(ValueError):reader.SqlGroupStore('no-db',{'C'},{'V'},keys)


@pytest.mark.parametrize('fault',[None,'missing','retired','foreign-endpoint','wrong-collection'])
def test_actual_sql_relation_read_is_explicit_complete_current_and_bounded(postgres_store,fault):
    s=postgres_store
    rows=[('claims','C',{'claim_id':'C'}),('canonical_viewpoints','V',{'viewpoint_id':'V'}),
          ('canonical_viewpoints','W',{'viewpoint_id':'W'}),('viewpoint_relations','REL',
            {'viewpoint_relation_id':'REL','source_viewpoint_id':'V','target_viewpoint_id':'W'})]
    if fault=='foreign-endpoint':rows[-1][2]['target_viewpoint_id']='outside'
    with s.connect() as conn:
        for col,oid,payload in rows:
            conn.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload,retired_at) VALUES (%s,%s,1,%s,%s::jsonb,CASE WHEN %s THEN now() END)",
                (col,oid,record_content_sha(payload),canonical_json(payload),fault=='retired' and oid=='REL'))
    key='viewpoint_relations/'+('missing' if fault=='missing' else 'REL')
    if fault=='wrong-collection':key='viewpoint_revisions/REL'
    if fault:
        with pytest.raises(ValueError):reader.SqlGroupStore(s.database_url,{'C'},{'V','W'},[key])
    else:
        ordinary=reader.SqlGroupStore(s.database_url,{'C'},{'V','W'})
        assert ('viewpoint_relations','REL') not in ordinary.read_pins
        complete=reader.SqlGroupStore(s.database_url,{'C'},{'V','W'},[key])
        assert complete.extra_record_keys==[key]
        assert complete.read_pins['viewpoint_relations','REL'][2] is False
        assert complete.collection('viewpoint_relations')[0]['payload']==rows[-1][2]
