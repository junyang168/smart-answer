import itertools
import numpy as np
import pytest
from backend.pipeline.monotonic_acoustic_decode import monotonic_path


def test_global_optimum_matches_exhaustive_legal_acoustic_paths():
    logits=np.array([[0,2,5,1],[1,6,2,0],[0,2,7,6]],dtype=float)
    path,info=monotonic_path(logits,3)
    legal=[p for p in itertools.product(range(4),repeat=3) if list(p)==sorted(p)]
    optimum=max(sum(logits[i,p[i]] for i in range(3)) for p in legal)
    assert info['score']==optimum
    assert path==sorted(path)
    assert all(type(c) is int for c in path)


def test_never_selects_outside_audio_and_preserves_quantized_points():
    path,_=monotonic_path([[0,9,50],[0,9,50]],1)
    assert path==[1,1]
    with pytest.raises(ValueError):monotonic_path([[float('nan')]],0)
