"""Source-bound projected screen proposals; preserve full-frame context."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from backend.pipeline.slide_change_pilot import read_json,save_json,file_sha,object_sha
from backend.pipeline.slide_pilot_models import VisionReviewer
from backend.pipeline.transcript_audio_pilot import saved_call

PROMPT='''Locate the physical projection screen in each lecture frame, using only visible geometry.
Return JSON results in supplied frame ID order. Each result: frame_id, quad (null when no
screen is visible; otherwise four [x,y] corner points TL,TR,BR,BL on the WHOLE image 0..1000
coordinate grid), reason. Use the inner projection surface, excluding the room, frame,
lecturer, lectern and subtitles. A lecturer partly occluding the screen is not screen
motion. Do not invent hidden slide text. This is only a crop proposal; full frames remain
available to slide classification. No words or timestamps are to be transcribed.'''


def validate_quad(value):
    if value is None:return None
    points=np.asarray(value,dtype=float)
    if points.shape==(8,):points=points.reshape(4,2)
    if points.shape!=(4,2) or not np.isfinite(points).all() or np.any(points<0) or np.any(points>1000):
        raise ValueError('Invalid screen corners')
    if not cv2.isContourConvex(points.astype(np.float32)) or abs(cv2.contourArea(points.astype(np.float32)))<30000:
        raise ValueError('Degenerate screen proposal')
    if points[0,1]>=points[3,1] or points[1,1]>=points[2,1] or points[0,0]>=points[1,0]:
        raise ValueError('Screen corners must be TL,TR,BR,BL')
    return points.tolist()


def rectify(frame, points):
    points=np.asarray(points,dtype=np.float32)
    width=round(max(np.linalg.norm(points[1]-points[0]),np.linalg.norm(points[2]-points[3])))
    height=round(max(np.linalg.norm(points[3]-points[0]),np.linalg.norm(points[2]-points[1])))
    if min(width,height)<2:raise ValueError('Empty projected screen')
    target=np.float32([[0,0],[width-1,0],[width-1,height-1],[0,height-1]])
    return cv2.warpPerspective(frame,cv2.getPerspectiveTransform(points,target),(width,height))


def calibrate(folder:Path):
    path=folder/'manifest.json';manifest=read_json(path)
    if 'screen_calibration_sha256' in manifest:
        if manifest['screen_calibration_sha256']!=file_sha(folder/'screen_calibration.json'):
            raise ValueError('Screen calibration changed')
        return manifest
    frames=manifest['frames']
    if not frames:raise ValueError('No source frames to calibrate')
    count=min(9,len(frames))
    selected=[frames[round(i*(len(frames)-1)/max(1,count-1))] for i in range(count)]
    adapter=VisionReviewer('gemini',PROMPT,{})
    adapter.config.pop('response_json_schema');adapter.config.update(max_output_tokens=8192,thinking_config={'thinking_level':'LOW'})
    ids=[r['id'] for r in selected];parts=adapter.contents(ids,[folder/r['image'] for r in selected])
    request={'stage':'projected_screen_proposals','prompt':PROMPT,'model':adapter.model,'config':adapter.config,
             'source_manifest_sha256':file_sha(path),'frames':[{'id':r['id'],'image_sha256':r['sha256']} for r in selected]}
    raw=saved_call(folder/'calibration',request,.06,lambda:adapter.generate(parts),max_calls=2,budget_usd=.15)
    answer=json.loads(adapter.text(raw));rows=answer if isinstance(answer,list) else answer['results']
    if [r['frame_id'] for r in rows]!=ids:raise ValueError('Screen calibration IDs drifted')
    proposals=[]
    for r,f in zip(rows,selected):proposals.append({**r,'quad':validate_quad(r['quad']),'time_ms':f['time_ms']})
    evidence={'source_manifest_sha256':file_sha(path),'model':adapter.model,'request_sha256':object_sha(request),
              'proposals':proposals,'estimated_usd_upper':adapter.usage(raw)['estimated_usd_upper'],
              'status':'machine_crop_proposals_not_slide_presence_verification'}
    save_json(folder/'screen_calibration.json',evidence)
    for row in frames:
        nearest=min(proposals,key=lambda r:abs(r['time_ms']-row['time_ms']))
        if nearest['quad'] is None:continue
        quad=[[x*manifest['original_width']/1000,y*manifest['original_height']/1000] for x,y in nearest['quad']]
        xs,ys=zip(*quad);x,y=round(min(xs)),round(min(ys))
        row['roi_original_xywh']=[x,y,round(max(xs))-x,round(max(ys))-y]
        row['screen_quad_original_xy']=quad
        row['screen_proposal_frame_id']=nearest['frame_id']
    manifest['screen_calibration_sha256']=file_sha(folder/'screen_calibration.json')
    manifest['limitations'].append('Screen geometry uses nearest uniform sampled machine proposal; full-frame context remains available.')
    save_json(path,manifest)
    return manifest
