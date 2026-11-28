"""Verified reuse or versioned playback; source files are never overwritten."""
from pathlib import Path
import json
import subprocess
from .runtime import atomic, file_sha
from .store import key
from .planner import probe


def mp4_faststart(path):
    with path.open('rb') as f:
        while header:=f.read(8):
            if len(header)!=8:return False
            size=int.from_bytes(header[:4],'big');kind=header[4:]
            if kind==b'moov':return True
            if kind==b'mdat':return False
            if size==1:
                size=int.from_bytes(f.read(8),'big');size-=8
            if size<8:return False
            f.seek(size-8,1)
    return False


def compatible(path, video):
    pr=probe(path);streams=pr['streams']
    if video:
        v=next(s for s in streams if s['codec_type']=='video')
        a=next(s for s in streams if s['codec_type']=='audio')
        return (v.get('codec_name')=='h264' and v['width']<=1280 and v['height']<=720
                and a.get('codec_name')=='aac' and mp4_faststart(path))
    return path.suffix.lower()=='.mp3'


def playback(payload):
    from .stages import media, playback_anchors
    out=Path(payload['out']);cfg=payload['config'];info=payload['info']
    source=Path(info['source']);receipt=out/'media-receipt.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        if saved['source_sha256']!=info['source_sha'] or file_sha(Path(saved['path']))!=saved['sha256']:
            raise ValueError('Playback receipt/source drift')
        return saved
    # Identical source bytes can be reused without guessing same-recording identity.
    if compatible(source,info['video']):
        subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(source),'-f','null','-'],check=True,capture_output=True)
        anchors=playback_anchors(source,source,info['duration'])
        result={'path':str(source),'sha256':info['source_sha'],'source_sha256':info['source_sha'],
                'mime':'video/mp4' if info['video'] else 'audio/mpeg','duration':info['duration'],
                'anchors':anchors,'origin':'verified_existing'}
        atomic(receipt,result)
        return result
    version=key([info['source_sha'],'h264-aac-720p-crf23-v1'])[:24]
    root=Path(cfg['media_root'])/'playback'/payload['item']/version
    # Reuse the established encode, decode, anchor, disk and no-clobber protocol.
    result=media({**payload,'config':{**cfg,'media_root':str(root)}})
    return result
