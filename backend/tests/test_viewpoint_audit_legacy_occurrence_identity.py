import pytest
from backend.pipeline.viewpoint_audit_source_adjudication import occurrence_source_ids, validate_own_source_identity

@pytest.mark.parametrize('occurrence,wanted',[({'source_id':'source','transcript_id':'alias'}, {'source'}),
    ({'transcript_id':'registered-sermon'}, {'registered-sermon'}),({'source_id':'notes_manuscript:x'},{'notes_manuscript:x'})])
def test_exact_declared_keys_only(occurrence,wanted):
    assert occurrence_source_ids({'occurrences':[occurrence]})==wanted

@pytest.mark.parametrize('occurrences',[[],[{}],[{'lecture':'lecture'}],[{'title':'title'}],[{'path':'file'}],[{'transcript_id':' '}],[{'source_id':123}],[{'source_id':'valid'},{}]])
def test_missing_or_title_guesses_do_not_qualify(occurrences):
    with pytest.raises(ValueError):occurrence_source_ids({'occurrences':occurrences})

@pytest.mark.parametrize('case',['unique','ambiguous','retired','foreign','notes'])
def test_legacy_key_still_requires_unique_active_registered_sermon(case):
    source={'source_id':'physical','transcript_id':'registered-sermon','source_type':'sermon_transcript'}
    records=[source]
    if case=='ambiguous':records.append(dict(source,source_id='other'))
    if case=='notes':source=dict(source,source_type='notes_manuscript');records=[source]
    class Store:
        def list_records(self,c):return records
        def get_record_state(self,c,sid):return {'retired':case=='retired','payload':next(s for s in records if s['source_id']==sid)}
    keys=occurrence_source_ids({'occurrences':[{'transcript_id':'foreign' if case=='foreign' else 'registered-sermon'}]})
    if case=='unique':validate_own_source_identity(source,keys,Store())
    else:
        with pytest.raises(ValueError,match='foreign source'):validate_own_source_identity(source,keys,Store())
