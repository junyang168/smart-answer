from pathlib import Path
import importlib.util
import sys
import uuid
import pytest
import psycopg
from psycopg.types.json import Jsonb
from backend.tests.test_registry_integrity_baseline_postgres import database
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres

path=Path(__file__).resolve().parents[2]/'scripts/audit-readset.py'
spec=importlib.util.spec_from_file_location('independent_readset_test',path)
reader=importlib.util.module_from_spec(spec);sys.modules[spec.name]=reader;spec.loader.exec_module(reader)


@pytest.fixture(scope='module',autouse=True)
def index(database):
    with psycopg.connect(database) as conn:conn.execute(path.with_name('audit-readset-index.sql').read_text())


def put(conn,col,oid,payload,retired=False):
    conn.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload,retired_at) "
        "VALUES(%s,%s,1,%s,%s,CASE WHEN %s THEN now() END) ON CONFLICT(collection,object_id) "
        "DO UPDATE SET payload=excluded.payload,retired_at=excluded.retired_at",
        (col,oid,'a'*64,Jsonb(payload),retired))


@pytest.fixture
def graph(database):
    prefix=uuid.uuid4().hex
    ids={n:prefix+'-'+n for n in ('C','C2','V','R','L','L2','E','ER','F','FR','S')}
    with psycopg.connect(database) as conn:
        put(conn,'claims',ids['C'],{'claim_id':ids['C'],'eligible_evidence_step_ids':[ids['E'],ids['ER']]})
        put(conn,'claims',ids['C2'],{'claim_id':ids['C2'],'evidence_step_ids':[ids['ER']]})
        put(conn,'canonical_viewpoints',ids['V'],{'viewpoint_id':ids['V'],'current_revision_id':ids['R']})
        put(conn,'viewpoint_revisions',ids['R'],{'viewpoint_revision_id':ids['R'],'core_proposition':'fixture'})
        put(conn,'viewpoint_claim_links',ids['L'],{'viewpoint_id':ids['V'],'claim_id':ids['C'],
            'link_type':'equivalent_component','effective_state':'active','evidence_bindings':[{'source_fragment_id':ids['F']}]})
        put(conn,'viewpoint_claim_links',ids['L2'],{'viewpoint_id':ids['V'],'claim_id':ids['C2'],
            'link_type':'supports','effective_state':'active','evidence_bindings':[{'source_fragment_id':ids['FR']}]})
        put(conn,'evidence_steps',ids['E'],{'evidence_step_id':ids['E'],'source_fragment_ids':[ids['F']]})
        put(conn,'evidence_steps',ids['ER'],{'evidence_step_id':ids['ER'],'source_fragment_id':ids['FR'],
                                           'support_eligibility':'withheld_unreviewed'},retired=True)
        put(conn,'source_fragments',ids['F'],{'fragment_id':ids['F'],'source_id':ids['S']})
        put(conn,'source_fragments',ids['FR'],{'fragment_id':ids['FR'],'source_id':ids['S']},retired=True)
        put(conn,'source_documents',ids['S'],{'source_id':ids['S'],'review_status':'human_review_required'})
    return ids


def test_sql_complete_selected_members_support_links_retired_evidence_and_holds(database,graph):
    frame=reader.AuditReadset(database,[graph['C']],[graph['V']])
    assert set(frame.rows['claims'])=={graph['C'],graph['C2']}
    assert len(frame.collection('viewpoint_claim_links'))==2
    assert len(frame.collection('evidence_steps',include_retired=True))==2
    assert len(frame.collection('evidence_steps'))==1
    assert frame.rows['evidence_steps'][graph['ER']]['payload']['support_eligibility']=='withheld_unreviewed'
    assert frame.collection('source_documents')[0]['payload']['review_status']=='human_review_required'
    assert frame.metrics['query_plans']==7
    assert frame.assert_unchanged()['query_plans']==7


@pytest.mark.parametrize('change',['phantom_member','retire_member','update_claim','source_metadata','retire_fragment','same_payload_update'])
def test_closing_boundary_detects_actual_dependency_or_reverse_link_change(database,graph,change):
    frame=reader.AuditReadset(database,[graph['C']],[graph['V']])
    with psycopg.connect(database) as conn:
        if change=='phantom_member':
            put(conn,'viewpoint_claim_links',uuid.uuid4().hex,{'viewpoint_id':graph['V'],'claim_id':'missing',
                'link_type':'qualifies','effective_state':'active'})
        else:
            collection,key={'retire_member':('viewpoint_claim_links','L'),
                'update_claim':('claims','C'),'source_metadata':('source_documents','S'),
                'retire_fragment':('source_fragments','F'),'same_payload_update':('claims','C')}[change]
            if change.startswith('retire'):
                conn.execute('UPDATE wang_knowledge.objects SET retired_at=now() WHERE collection=%s AND object_id=%s',
                             (collection,graph[key]))
            elif change=='same_payload_update':
                conn.execute('UPDATE wang_knowledge.objects SET payload=payload WHERE collection=%s AND object_id=%s',
                             (collection,graph[key]))
            else:
                conn.execute("UPDATE wang_knowledge.objects SET payload=payload||'{\"new_hold\":true}'::jsonb "
                             'WHERE collection=%s AND object_id=%s',(collection,graph[key]))
    with pytest.raises(ValueError,match='drift|missing'):frame.assert_unchanged()


def test_unrelated_growth_keeps_selected_sql_rows_fixed(database,graph):
    observations=[]
    for n in (1,9,90):
        with psycopg.connect(database) as conn:
            for _ in range(n):put(conn,'claims','unrelated-'+uuid.uuid4().hex,{'claim_id':'unrelated','statement':'unrelated'*100})
            conn.execute('ANALYZE wang_knowledge.objects')
        frame=reader.AuditReadset(database,[graph['C']],[graph['V']])
        observations.append(frame.metrics['sql_result_rows'])
    assert len(set(observations))==1


def test_long_unrelated_revision_history_with_same_viewpoint_cannot_expand_member_readset(database,graph):
    observations=[]
    for extra in (1,9,90):
        with psycopg.connect(database) as conn:
            for _ in range(extra):
                put(conn,'viewpoint_revisions','historical-'+uuid.uuid4().hex,
                    {'viewpoint_id':graph['V'],'core_proposition':'unrelated old revision'*100})
            conn.execute('ANALYZE wang_knowledge.objects')
        frame=reader.AuditReadset(database,[graph['C']],[graph['V']])
        observations.append(frame.metrics['sql_result_rows'])
        assert len(frame.rows['viewpoint_revisions'])==1
        assert len(frame.rows['viewpoint_claim_links'])==2
    assert len(set(observations))==1


def test_missing_or_recreated_partial_index_is_never_auto_installed_or_treated_unchanged(database,graph):
    frame=reader.AuditReadset(database,[graph['C']],[graph['V']])
    try:
        with psycopg.connect(database) as conn:conn.execute('DROP INDEX wang_knowledge.objects_audit_viewpoint_members_idx')
        with pytest.raises(ValueError,match='explicit maintenance'):reader.AuditReadset(database,[graph['C']],[graph['V']])
    finally:
        with psycopg.connect(database) as conn:conn.execute(path.with_name('audit-readset-index.sql').read_text())
    with pytest.raises(ValueError,match='drift'):frame.assert_unchanged()


def test_dangling_forward_dependency_cannot_shrink_the_frame(database,graph):
    with psycopg.connect(database) as conn:
        put(conn,'claims',graph['C'],{'claim_id':graph['C'],'evidence_step_ids':['missing-original-evidence']})
    with pytest.raises(ValueError,match='missing'):reader.AuditReadset(database,[graph['C']],[graph['V']])


@pytest.mark.parametrize('node',[{'Node Type':'Seq Scan','Relation Name':'objects'},
    {'Node Type':'Parallel Seq Scan','Relation Name':'objects'},
    {'Node Type':'Index Scan','Relation Name':'objects'},
    {'Node Type':'Index Only Scan','Relation Name':'objects'},
    {'Node Type':'Bitmap Heap Scan','Relation Name':'objects'}])
def test_full_table_and_full_index_plan_veto(node):
    with pytest.raises(ValueError):reader.indexed_plan([{'Plan':node}])


@pytest.mark.parametrize('kind',['Index Scan','Index Only Scan','Bitmap Heap Scan'])
def test_collection_index_plus_payload_filter_still_scales_with_unrelated_records_and_is_refused(kind):
    node={'Node Type':kind,'Relation Name':'objects','Index Cond':"collection='viewpoint_claim_links'",
          'Recheck Cond':"collection='viewpoint_claim_links'",'Filter':'payload @> target'}
    with pytest.raises(ValueError,match='collection-only'):reader.indexed_plan([{'Plan':node}],'payload')


def test_payload_gin_and_exact_primary_key_plan_conditions_are_targeted():
    reader.indexed_plan([{'Plan':{'Node Type':'Index Scan','Relation Name':'objects','Index Cond':'object_id = ANY(ids)'}}])
    reader.indexed_plan([{'Plan':{'Node Type':'Bitmap Heap Scan','Relation Name':'objects','Recheck Cond':'payload @> target'}}],'payload')


def test_no_backend_dependency_in_independent_sql_module():
    import ast
    parsed=ast.parse(path.read_text())
    assert not any(isinstance(n,ast.ImportFrom) and (n.module or '').startswith('backend') for n in ast.walk(parsed))
    assert not any(isinstance(n,ast.Import) and any(a.name.startswith('backend') for a in n.names) for n in ast.walk(parsed))


@pytest.mark.parametrize('ids',[[None],[''],['x'*513],['x']*10001])
def test_bad_or_unbounded_identity_stops_before_SQL(ids,monkeypatch):
    monkeypatch.setattr(reader.subprocess,'run',lambda *a,**k:pytest.fail('no SQL for invalid input'))
    with pytest.raises(ValueError):reader.AuditReadset('not-a-db',ids,['V'])
