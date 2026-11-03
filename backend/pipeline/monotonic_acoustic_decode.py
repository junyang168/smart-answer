"""Maximum-score monotonic path through actual timestamp-class logits.

Selects model classes, never interpolates times or fabricates word durations.
Equal quantized classes are allowed. Decoder confidence is retained for gates.
"""
import numpy as np


def monotonic_path(logits, max_class):
    values=np.asarray(logits,dtype=np.float32)
    if values.ndim!=2 or not values.shape[0] or not np.isfinite(values).all():
        raise ValueError('Finite timestamp logits are required')
    count=min(values.shape[1],int(max_class)+1)
    if count<=0:raise ValueError('No timestamp class lies inside source audio')
    values=values[:,:count]
    back=np.zeros(values.shape,dtype=np.int32);scores=values[0].copy();indices=np.arange(count,dtype=np.int32)
    for i in range(1,len(values)):
        prefix=np.maximum.accumulate(scores)
        records=np.r_[True,scores[1:]>prefix[:-1]]
        best=np.maximum.accumulate(np.where(records,indices,0))
        back[i]=best;scores=values[i]+prefix
    selected=np.zeros(len(values),dtype=np.int32);selected[-1]=scores.argmax()
    for i in range(len(values)-1,0,-1):selected[i-1]=back[i,selected[i]]
    chosen=values[np.arange(len(values)),selected]
    regret=values.max(axis=1)-chosen
    return selected.tolist(),{'profile':'monotonic-logit-path.v1','mean_logit_regret':float(regret.mean()),
                              'max_logit_regret':float(regret.max()),'per_coordinate_logit_regret':regret.tolist(),
                              'score':float(chosen.sum()),'max_allowed_class':count-1}
