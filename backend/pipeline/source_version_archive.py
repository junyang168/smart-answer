"""Preserve exact transcript bytes before the editor replaces them."""
import hashlib
import os
import tempfile
from pathlib import Path


def archive_bytes(base_folder, raw):
    path = Path(base_folder) / 'wang-knowledge-platform' / 'source-versions' / (hashlib.sha256(raw).hexdigest() + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish only complete bytes. A crash must leave a disposable temp file,
    # never a truncated file at the content-addressed archive name.
    descriptor, temporary = tempfile.mkstemp(prefix='.source-version-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError('immutable source version archive drift')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
