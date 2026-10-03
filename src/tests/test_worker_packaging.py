"""Public packaging fixtures; synthetic source/wheel inputs, no authentication."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module

@pytest.fixture
def backend():return load(ROOT/'build_support/sspi_backend.py','fixture_backend')

def test_backend_restores_metadata_and_external_target_after_forced_failure(tmp_path,monkeypatch,backend):
    class Producer:
        def identify(self,*args,**kwargs):return '42'*32,tmp_path/'Cargo.toml',{}
    monkeypatch.chdir(ROOT);monkeypatch.setattr(backend,'producer',lambda *args:Producer())
    monkeypatch.setattr(backend,'settings',lambda _: (['--locked'],'synthetic','release',[],{}))
    def failed(*args,**kwargs):
        import os
        observed=os.environ.get('GWZ_SSPI_BUILD_FINGERPRINT')
        assert observed==prior
        assert kwargs['env']['GWZ_SSPI_BUILD_FINGERPRINT']=='42'*32
        raise RuntimeError('forced build failure')
    monkeypatch.setattr(backend.subprocess,'run',failed)
    for prior in [None,'prior']:
        for name in ['GWZ_SSPI_BUILD_FINGERPRINT','CARGO_TARGET_DIR']:
            if prior is None:monkeypatch.delenv(name,raising=False)
            else:monkeypatch.setenv(name,prior)
        with pytest.raises(RuntimeError,match='forced'):backend.build_wheel(str(tmp_path))
        import os
        assert os.environ.get('GWZ_SSPI_BUILD_FINGERPRINT')==prior
        assert os.environ.get('CARGO_TARGET_DIR')==prior

def test_producer_discovery_is_cargo_resolved_and_duplicate_refuses(monkeypatch,backend):
    monkeypatch.setattr(backend.subprocess,'check_output',lambda _:json.dumps({'packages':[{'name':'gwz-sspi'},{'name':'gwz-sspi'}]}).encode())
    with pytest.raises(RuntimeError,match='ambiguous'):backend.producer(Path('/extracted/Cargo.toml'))

def test_rejected_build_options_do_not_create_a_fictitious_fingerprint(monkeypatch,backend):
    monkeypatch.chdir(ROOT)
    for value in ['--profile unsupported','--zig','--target','--target one --target two','-- --cfg arbitrary','--release --profile dev','--features one -F two','--interpreter one -i two']:
        with pytest.raises(RuntimeError):backend.settings({'maturin.build-args':value})

def test_actual_loaded_extension_selection_ignores_attributes_environment_and_cwd(tmp_path):
    import os
    native=os.environ.get('GWZ_PY_NATIVE_MODULE')
    if not native:pytest.skip('requires a provisioned installed/extracted wheel')
    import shutil
    source=Path(native).resolve()
    package=tmp_path/'installed'/source.parent.name
    shutil.copytree(source.parent,package)
    native=str(package/source.name)
    script='''
import importlib.util,os,pathlib,sys
image=pathlib.Path(sys.argv[1]).resolve()
spec=importlib.util.spec_from_file_location('_gwz_core',image)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
expected=image.parent/('gwz-sspi-worker.exe' if os.name=='nt' else 'gwz-sspi-worker')
module.__file__='/untrusted/module.pyd';os.chdir(sys.argv[2])
os.environ['PATH']='';os.environ['GWZ_SSPI_NATIVE_WORKER']='/untrusted/worker'
os.environ['GWZ_SSPI_BUILD_FINGERPRINT']='00'*32
assert pathlib.Path(module.sspi_worker_descriptor())==expected
receipt=image.parent/'sspi-artifact-set.json'
receipt.write_text('untrusted sidecar')
assert pathlib.Path(module.sspi_worker_descriptor())==expected
hidden=expected.with_suffix('.held');expected.rename(hidden)
try:
    try:module.sspi_worker_descriptor()
    except RuntimeError as error:assert str(error)=='WorkerUnavailable'
    else:raise AssertionError('missing worker accepted')
finally:hidden.rename(expected)
'''
    subprocess.run([sys.executable,'-c',script,native,str(tmp_path)],check=True)

def test_worker_output_is_owned_by_one_build_even_with_shared_cache(tmp_path,monkeypatch,backend):
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor
    barrier=threading.Barrier(2)
    class Producer:
        def identify(self,*args,**kwargs):
            identifier=threading.current_thread().name.encode().hex().ljust(64,'0')[:64]
            return identifier,tmp_path/'Cargo.toml',{}
        def bundle(self,wheel,binary,identifier,inputs):
            assert binary.read_text()==identifier
    monkeypatch.setattr(backend,'producer',lambda *args:Producer())
    monkeypatch.setattr(backend,'settings',lambda _: (['--locked'],'synthetic','release',[],{}))
    monkeypatch.setenv('CARGO_TARGET_DIR',str(tmp_path/'shared'))
    before=dict(os.environ)
    def build(command,*,env,check):
        binary=Path(env['CARGO_TARGET_DIR'])/'synthetic/release/gwz-sspi-worker'
        binary.parent.mkdir(parents=True,exist_ok=True)
        binary.write_text(env['GWZ_SSPI_BUILD_FINGERPRINT'])
        barrier.wait()
    monkeypatch.setattr(backend.subprocess,'run',build)
    monkeypatch.setattr(backend,'maturin_wheel',lambda *a,**k:threading.current_thread().name+'.whl')
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _:backend.build_wheel(str(tmp_path)),range(2)))
    assert dict(os.environ)==before

def test_editable_retains_hooks_and_is_explicitly_unprovisioned(tmp_path,monkeypatch,backend):
    monkeypatch.setenv('GWZ_SSPI_BUILD_FINGERPRINT','42'*32)
    monkeypatch.setenv('GWZ_SSPI_PACKAGING_TARGETS','synthetic=metadata')
    def editable(directory,args,environment,metadata_directory,editable):
        assert editable
        assert 'GWZ_SSPI_BUILD_FINGERPRINT' not in environment
        assert 'GWZ_SSPI_PACKAGING_TARGETS' not in environment
        return 'editable.whl'
    monkeypatch.setattr(backend,'maturin_wheel',editable)
    assert backend.build_editable(str(tmp_path))=='editable.whl'
    assert callable(backend.get_requires_for_build_editable)
    assert callable(backend.prepare_metadata_for_build_editable)


def test_actual_publish_steps_use_provisioned_wheel_and_backend_carrying_sdist(tmp_path):
    support=tmp_path/'build_support';support.mkdir()
    (support/'sspi_backend.py').write_text("import json\nfrom pathlib import Path\ndef build_wheel(directory,config):Path('wheel-call.json').write_text(json.dumps([directory,config]))\ndef build_sdist(directory):Path('sdist-call.json').write_text(json.dumps(directory))\n")
    lines=(ROOT/'.github/workflows/publish.yml').read_text().splitlines()
    for title in ['Build wheel','Build source distribution']:
        start=lines.index('      - name: '+title)
        block=next(i for i in range(start,len(lines)) if lines[i].strip()=='run: |')
        body=[]
        for line in lines[block+1:]:
            if line.strip() and not line.startswith('          '):break
            body.append(line[10:])
        script='\n'.join(body).replace('${{ matrix.auditwheel }}','--auditwheel=skip')
        subprocess.run([sys.executable,'-c',script],cwd=tmp_path,check=True)
    assert json.loads((tmp_path/'wheel-call.json').read_text())==['dist',{'maturin.build-args':'--release --locked --auditwheel=skip'}]
    assert json.loads((tmp_path/'sdist-call.json').read_text())=='dist'
