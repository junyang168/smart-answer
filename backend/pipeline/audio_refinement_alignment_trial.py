"""Compare official and raw decoding from the SAME final-text Qwen forward pass.

Run with the existing isolated alignment interpreter. No model API calls.
"""
import argparse
import json
import time
import wave
from pathlib import Path


def run(out,capacity,device='mps'):
    from backend.pipeline.media_batch.runtime import slot,atomic,file_sha
    import numpy as np
    import torch
    from transformers import AutoModelForTokenClassification,AutoProcessor
    model_id='Qwen/Qwen3-ForcedAligner-0.6B-hf';revision='c07281df297b9905d24a508279258cccf987a064'
    root=out/'alignment-input';asr=json.loads((root/'asr.json').read_text())
    binding={'asr_sha256':file_sha(root/'asr.json'),'model':model_id,'revision':revision,'device':device}
    receipt=out/('alignment-trial.json' if device=='mps' else 'alignment-trial-cpu.json')
    if receipt.exists():
        prior=json.loads(receipt.read_text())['binding']
        # Initial trial was fixed MPS/float16 before the CPU comparison existed.
        if {**prior,'device':prior.get('device','mps')}!=binding:raise ValueError('Alignment input drift')
        return
    with slot(capacity,'mps',1):
        processor=AutoProcessor.from_pretrained(model_id,revision=revision,local_files_only=True)
        model=AutoModelForTokenClassification.from_pretrained(model_id,revision=revision,
            local_files_only=True,dtype=torch.float16 if device=='mps' else torch.float32,attn_implementation='sdpa').to(device).eval()
        summaries=[]
        for chunk in asr['chunks']:
            path=root/chunk['audio']
            if file_sha(path)!=chunk['audio_sha256']:raise ValueError('Audio drift')
            with wave.open(str(path)) as f:
                assert (f.getframerate(),f.getnchannels(),f.getsampwidth())==(16000,1,2)
                audio=np.frombuffer(f.readframes(f.getnframes()),'<i2').astype(np.float32)/32768
            assert len(audio)<=300*16000
            started=time.perf_counter()
            inputs,words=processor.prepare_forced_aligner_inputs(audio=audio,transcript=chunk['text'],language='Chinese',sampling_rate=16000)
            inputs=inputs.to(model.device,model.dtype)
            with torch.inference_mode():result=model(**inputs)
            if not torch.isfinite(result.logits).all():raise ValueError('Invalid alignment logits')
            mask=inputs['input_ids'][0]==model.config.timestamp_token_id
            classes=result.logits.argmax(-1)[0][mask].cpu().tolist();step=processor.timestamp_segment_time
            raw=[dict(text=w,start_ms=round(classes[2*i]*step),end_ms=round(classes[2*i+1]*step)) for i,w in enumerate(words[0])]
            official=processor.decode_forced_alignment(result.logits,inputs['input_ids'],words,model.config.timestamp_token_id)[0]
            fixed=[dict(text=w['text'],start_ms=round(w['start_time']*1000),end_ms=round(w['end_time']*1000)) for w in official]
            identity={'text_sha256':__import__('hashlib').sha256(chunk['text'].encode()).hexdigest(),'audio_sha256':file_sha(path),'config':binding}
            for label,items in [('raw',raw),('official',fixed)]:
                name='alignment' if label=='raw' else 'official-alignment'
                folder=root/(name if device=='mps' else 'cpu-'+name)
                atomic(folder/f"{chunk['id']}.json",dict(identity=identity,items=items))
            def invalid(items):
                return sum(not 0<=w['start_ms']<w['end_ms']<=len(audio)/16 or i>0 and w['start_ms']<items[i-1]['start_ms'] for i,w in enumerate(items))
            delta=[max(abs(a['start_ms']-b['start_ms']),abs(a['end_ms']-b['end_ms'])) for a,b in zip(raw,fixed)]
            summaries.append(dict(id=chunk['id'],tokens=len(raw),raw_invalid=invalid(raw),official_invalid=invalid(fixed),
                official_changed_tokens=sum(d>0 for d in delta),maximum_change_ms=max(delta),wall_seconds=time.perf_counter()-started))
            del inputs,result
            if device=='mps':torch.mps.empty_cache()
            print(json.dumps(summaries[-1]),flush=True)
        atomic(receipt,dict(binding=binding,chunks=summaries,official_postprocessing_disclosed=True,ground_truth_available=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--capacity',type=Path,required=True)
    p.add_argument('--device',choices=['mps','cpu'],default='mps')
    a=p.parse_args();run(a.out,a.capacity,a.device)
