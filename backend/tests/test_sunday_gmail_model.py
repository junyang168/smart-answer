"""Opt-in matching evaluations with mail-shaped packets, no mailbox or file writes."""
import os
import pytest
from backend.sunday_assistant.gmail_source import classify, verify

pytestmark=pytest.mark.skipif(os.getenv('RUN_SUNDAY_AGENT_MODEL_TESTS')!='1',reason='real model evaluation is opt-in')


def packet(subject,filename,body='Dear Jun and Jianwen, Please see the attached powerpoint. Blessings, Sonny.',at='2026-10-31T18:00:00-05:00'):
    return {'id':'synthetic-message','from':'Sonny Chuah <pastor@example.com>', 'subject':subject,'body':body,'sent_at':at,
            '_verified_sender_worker':'蔡尚立 牧師','attachments':[{'part_id':'1','filename':filename,'size':1000}]}


def test_model_reads_mmddyy_attachment_date_and_confirmed_email_identity():
    message=packet('聖靈充滿與福音廣傳 （徒1-2章）','聖靈充滿與福音廣傳 （徒1-2章）110126 Latest.pptx')
    calendar=[{'date':'2026-11-01','speaker':'蔡尚立 牧師','title':'聖靈充滿與福音廣傳'},
              {'date':'2026-11-08','speaker':'蔡尚立 牧師','title':'喜樂的感恩——住棚節'}]
    result=classify(message,calendar)
    assert len(result.attachments)==1
    item=result.attachments[0]
    assert item.kind=='speaker' and str(item.service_date)=='2026-11-01' and item.speaker=='蔡尚立 牧師'
    assert verify(message,calendar,item).supported


def test_model_does_not_mistake_church_worship_deck_for_sermon():
    message=packet('圣道教會2026-11-01的主日崇拜同工安排及ppt','聖道教會2026-11-01主日崇拜.pptx',
                   body='本週服事同工：講道 蔡尚立 牧師，司會、領詩、司琴、讀經；附件為主日崇拜流程及詩歌。')
    message['_verified_sender_worker']=None
    result=classify(message,[{'date':'2026-11-01','speaker':'蔡尚立 牧師','title':'聖靈充滿與福音廣傳'}])
    assert result.attachments[0].kind in {'worship','unrelated'}


def test_model_does_not_silently_promote_explicit_old_reference_as_current_sermon():
    message=packet('11/8 喜樂的感恩——住棚節','天行—與神同行 創5_21-29 100426.pptx',
                   body='Attached is last month\'s old October 4 PPT for reference, not the November 8 sermon. The new file is not ready yet.',
                   at='2026-11-07T18:00:00-06:00')
    calendar=[{'date':'2026-11-08','speaker':'蔡尚立 牧師','title':'喜樂的感恩——住棚節'}]
    item=classify(message,calendar).attachments[0]
    assert item.kind!='speaker' or not verify(message,calendar,item).supported
