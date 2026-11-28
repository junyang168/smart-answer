"""Internal-disk model-call facts survive disappearance of the media SSD."""
import json,os,re,hashlib
from pathlib import Path
from .slide_change_pilot import object_sha
from .media_batch.store import key


def save(path,value):
    data=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
    if path.exists():
        if path.read_bytes()!=data:raise ValueError('Internal model-call mirror drift')
        return
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with path.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())


def prepare(out,request):
    context=os.getenv('MEDIA_BATCH_CONTEXT')
    if not context:return None
    ctx=json.loads(context);batch=ctx['batch'];task=ctx['task']
    if not re.fullmatch('B-[0-9a-f]{24}',batch) or not re.fullmatch('[0-9a-f]{64}',task):
        raise ValueError('Invalid internal model-call mirror identity')
    root=Path.home()/'.local/state/smart-answer-media-batches'/batch/'model-calls'/task/object_sha(request)
    if (root/'response.json').exists():
        raise ValueError('An internal saved answer exists; reconcile it before dispatching')
    save(root/'request.json',request)
    save(root/'identity.json',{'batch':batch,'task':task,'out':str(Path(out).resolve()),
                              'call':key([task,str(Path(out).resolve()),request])})
    return root


def response(root,value):
    if root is None:return
    save(root/'response.json',value)
    sha=hashlib.sha256((root/'response.json').read_bytes()).hexdigest()
    save(root/'response-proof.json',{'status':'response_saved','response_sha256':sha,
         'request_sha256':hashlib.sha256((root/'request.json').read_bytes()).hexdigest()})
