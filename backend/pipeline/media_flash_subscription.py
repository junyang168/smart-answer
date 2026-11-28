"""Native ACP image/text Flash subscription calls, with durable one-turn facts."""
from pathlib import Path
import json,os,queue,subprocess,threading,time,shutil,hashlib
from backend.pipeline.agy_subscription_client import subscription_environment,_resolve_executable
from backend.pipeline.media_batch.runtime import inherited_fds


class SubscriptionSetupError(RuntimeError):
    status_code=400  # Proven failure before any session/prompt, never an unknown dispatch.


class FlashSubscription:
    def __init__(self,folder,*,executable=None,timeout=240):
        self.folder=Path(folder);self.executable=executable or _resolve_executable();self.timeout=timeout
        self.serial=0;self.started=False;self.events=[];self.messages=queue.Queue();self.process=None

    def start(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        if (self.folder/'prompt-intent.json').exists():
            raise ValueError('Subscription prompt already dispatched; saved_call must govern replay')
        self.error=(self.folder/'stderr.log').open('a')
        self.log=(self.folder/'rpc.jsonl').open('a')
        binary=Path(shutil.which(self.executable) or self.executable).resolve(strict=True)
        self.identity={'executable_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'path':str(binary)}
        self.process=subprocess.Popen([self.executable,'acp','--include-default-customizations=false','--sandbox'],
            cwd=self.folder,env=subscription_environment(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=self.error,text=True,bufsize=1,pass_fds=inherited_fds())
        def read():
            for line in self.process.stdout:self.messages.put(line)
            self.messages.put(None)
        threading.Thread(target=read,daemon=True).start()
        response=self.rpc('initialize',{'protocolVersion':1,'clientCapabilities':{'fs':{
            'readTextFile':False,'writeTextFile':False},'terminal':False},'clientInfo':{'name':'wkp-media','version':'1'}})
        self.capabilities=response['agentCapabilities']['promptCapabilities']
        self.identity['agent_info']=response.get('agentInfo')
        return response

    def write(self,message):
        self.log.write(json.dumps({'direction':'client','message':message})+'\n');self.log.flush();os.fsync(self.log.fileno())
        self.process.stdin.write(json.dumps(message)+'\n');self.process.stdin.flush()

    def rpc(self,method,params):
        self.serial+=1;identity=self.serial
        self.write({'jsonrpc':'2.0','id':identity,'method':method,'params':params})
        deadline=time.monotonic()+self.timeout
        while True:
            if time.monotonic()>=deadline:raise TimeoutError('Subscription ACP response timed out')
            try:line=self.messages.get(timeout=max(.001,deadline-time.monotonic()))
            except queue.Empty:raise TimeoutError('Subscription ACP response timed out')
            if line is None:raise RuntimeError('Subscription ACP exited before response')
            event=json.loads(line);self.events.append(event)
            self.log.write(json.dumps({'direction':'agent','message':event})+'\n');self.log.flush();os.fsync(self.log.fileno())
            if 'method' in event and 'id' in event:
                if event['method']=='session/request_permission':
                    self.write({'jsonrpc':'2.0','id':event['id'],'result':{'outcome':{'outcome':'cancelled'}}})
                else:self.write({'jsonrpc':'2.0','id':event['id'],'error':{'code':-32601,'message':'Client tools disabled'}})
            if event.get('id')!=identity or 'method' in event:continue
            if 'error' in event:
                error=RuntimeError if self.started else SubscriptionSetupError
                raise error('Subscription ACP '+method+': '+json.dumps(event['error']))
            return event['result']

    def generate(self,model,parts,prompt,schema):
        from backend.pipeline.slide_visual_evidence import check_schema
        try:
            self.start()
            for part in parts:
                if part['type'] in ('audio','image') and not self.capabilities.get(part['type']):
                    raise SubscriptionSetupError('Subscription does not support '+part['type'])
            self.rpc('authenticate',{'methodId':'oauth-personal'})
            session=self.rpc('session/new',{'cwd':str(self.folder.resolve()),'mcpServers':[]})
            models=session.get('models',{})
            available={m['modelId'] for m in models.get('availableModels',[])}
            if model not in available:raise SubscriptionSetupError('Exact Flash subscription model unavailable: '+model)
            sid=session['sessionId']
            self.rpc('session/set_model',{'sessionId':sid,'modelId':model})
            content=[{'type':'text','text':prompt+'\nReturn only JSON matching this schema. Do not use tools.\n'+json.dumps(schema)},*parts]
            request={'sessionId':sid,'prompt':content}
            with (self.folder/'prompt-intent.json').open('x') as f:
                json.dump({'model':model,'request':request},f);f.flush();os.fsync(f.fileno())
            self.started=True
            result=self.rpc('session/prompt',request)
            if result.get('stopReason')!='end_turn':raise ValueError('Incomplete Flash subscription turn')
            chunks=[]
            for event in self.events:
                update=event.get('params',{}).get('update',{})
                if update.get('sessionUpdate') in ('tool_call','tool_call_update'):
                    raise ValueError('Flash subscription attempted a tool call')
                if update.get('sessionUpdate')=='agent_message_chunk':
                    block=update.get('content',{})
                    if block.get('type')=='text':chunks.append(block['text'])
            text=''.join(chunks).strip()
            if text.startswith('```json\n') and text.endswith('\n```'):text=text[8:-4]
            answer=json.loads(text);check_schema(answer,schema)
            return {'schema_version':'media-subscription-response.v1','transport':'agy_subscription',
                    'model':'gemini-3.8-flash','subscription_model':model,'status':'completed','answer':answer,
                    'capabilities':self.capabilities,'cli_identity':self.identity,'auth_method':'oauth-personal'}
        finally:self.close()

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        for name in ('error','log'):
            stream=getattr(self,name,None)
            if stream:stream.close()
