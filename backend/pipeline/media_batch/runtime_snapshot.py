"""Freeze committed worker code for long jobs without creating another git tree."""
from pathlib import Path
import io,json,os,shutil,subprocess,sys,tarfile,tempfile
from .store import Conflict


def runtime_root(config,root):
    root=Path(root)
    if config.get('pipeline')!='gemini-transcript-v1':return root
    # A packaged runtime or immutable deployment release already owns its code.
    if (root/'runtime-snapshot.json').exists() or not (root/'.git').exists():return root
    expected=config['implementation']
    parent=Path(config['artifact_root'])/'.runtime';parent.mkdir(parents=True,exist_ok=True)
    target=parent/expected
    def fingerprint(path):
        return subprocess.check_output([sys.executable,'-c',
            'from backend.pipeline.media_batch.gemini_plan import implementation; print(implementation())'],
            cwd=path,text=True,env={**os.environ,'PYTHONPATH':str(path)}).strip()
    if target.exists():
        if fingerprint(target)!=expected:raise Conflict('Saved runtime code drift')
        return target
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    archive=subprocess.check_output(['git','archive',commit,'backend','.python-version'],cwd=root)
    temporary=Path(tempfile.mkdtemp(prefix='.preparing-',dir=parent))
    try:
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:bundle.extractall(temporary,filter='data')
        for name in ('.env','backend/.env','backend/.venv'):
            source=root/name;dest=temporary/name
            if source.exists():dest.symlink_to(source.resolve(),target_is_directory=source.is_dir())
        if fingerprint(temporary)!=expected:
            raise Conflict('Planned code differs from committed runtime; commit the implementation and re-plan')
        (temporary/'runtime-snapshot.json').write_text(json.dumps({'git_commit':commit,'implementation':expected,
            'source_checkout':str(root),'schema_version':'media-runtime-snapshot.v1'}))
        try:temporary.rename(target)
        except FileExistsError:
            if fingerprint(target)!=expected:raise Conflict('Concurrent runtime snapshot differs')
        return target
    finally:
        if temporary.exists():shutil.rmtree(temporary)
