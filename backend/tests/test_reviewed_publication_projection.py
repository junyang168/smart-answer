from types import SimpleNamespace
import pytest
from backend.api.sc_api.sermon_manager import SermonManager
from backend.api.sc_api.transcript_review import atomic,read,file_hash


@pytest.mark.parametrize('remove_tags',[True,False])
def test_reviewed_publication_hides_deleted_text_from_readers_and_models(tmp_path,remove_tags):
    item='已校對講道';atomic(tmp_path/'transcript_review'/item/'context.json',{'revision':9})
    published={'metadata':{'title':'正式標題','review_revision':9},'script':[
        {'index':'h','type':'subtitle','text':'## 標題'},
        {'index':'1','text':'保留~~刪掉的內容~~結尾。','start_time':3.5,'end_time':8},
        {'index':'2','text':'~~整段\n刪掉~~','start_time':8,'end_time':10},
        {'index':'3','text':'~~自我更正，不是，~~正確句子。'},
        {'index':'4','text':'希臘文 λύτρον；英文 many = all。'}]}
    path=tmp_path/'script_published'/f'{item}.json';atomic(path,published);before=file_hash(path)
    manager=SermonManager.__new__(SermonManager);manager.base_folder=str(tmp_path)
    manager.get_sermon_permissions=lambda *_:SimpleNamespace(canRead=True)
    response=manager.get_final_sermon('reader',item,remove_tags=remove_tags)
    assert response['metadata']==published['metadata']
    rows={p['index']:p for p in response['script']}
    assert rows['1']['text']=='保留\n結尾。'
    assert not rows['2']['text'].strip() and rows['3']['text']=='\n正確句子。'
    assert rows['4']['text']==published['script'][4]['text']
    assert rows['1']['start_time']==3.5 and rows['1']['end_time']==8
    assert file_hash(path)==before and read(path)==published
    # This is also the frontend's Markdown-copy and model-input payload.
    payload='\n\n'.join(p['text'] for p in response['script'])
    assert '刪掉' not in payload and '自我更正' not in payload and '~~' not in payload
