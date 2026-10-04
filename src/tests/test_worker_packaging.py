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

@pytest.mark.parametrize('non_utf8',[False,True])
def test_actual_loaded_extension_selection_ignores_attributes_environment_and_cwd(tmp_path,non_utf8):
    import os
    native=os.environ.get('GWZ_PY_NATIVE_MODULE')
    if not native:pytest.skip('requires a provisioned installed/extracted wheel')
    if non_utf8 and os.name=='nt':pytest.skip('Unix filesystem-byte fixture')
    import shutil
    source=Path(native).resolve()
    package=tmp_path/(os.fsdecode(b'installed-\xff') if non_utf8 else 'installed')/source.parent.name
    try:shutil.copytree(source.parent,package)
    except OSError as error:
        import errno
        if non_utf8 and error.errno==errno.EILSEQ:pytest.skip('filesystem refuses non-UTF8 names; conversion is covered by Rust')
        raise
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
    def raw(directory,*args,**kwargs):
        name=threading.current_thread().name+'.whl';(Path(directory)/name).write_bytes(b'worker-specific fixture');return name
    monkeypatch.setattr(backend,'maturin_wheel',raw)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _:backend.build_wheel(str(tmp_path)),range(2)))
    assert dict(os.environ)==before

def test_editable_retains_hooks_and_is_explicitly_unprovisioned(tmp_path,monkeypatch,backend):
    monkeypatch.chdir(ROOT)
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

def test_frontend_32bit_target_and_interpreter_are_preserved(monkeypatch,backend):
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv('CARGO_BUILD_TARGET',raising=False)
    monkeypatch.setattr(backend.maturin,'get_config',lambda:{})
    monkeypatch.setattr(backend.maturin.platform,'system',lambda:'Windows')
    monkeypatch.setattr(backend.maturin.platform,'machine',lambda:'AMD64')
    monkeypatch.setattr(backend.maturin.struct,'calcsize',lambda _:4)
    monkeypatch.setattr(backend.subprocess,'check_output',lambda *a,**k:'host: x86_64-pc-windows-msvc\n')
    args,target,profile,_,_=backend.settings({})
    assert target=='i686-pc-windows-msvc'
    assert args[args.index('--interpreter')+1]==sys.executable
    assert profile=='release'

@pytest.fixture
def real_artifacts():
    import os
    source=ROOT.parent/'gwz-sspi/scripts/artifact_set.py'
    if not source.is_file():
        home=Path(os.environ.get('CARGO_HOME',Path.home()/'.cargo'))
        sources=list(home.glob('registry/src/*/gwz-sspi-0.1.0/scripts/artifact_set.py'))
        assert len(sources)==1,'resolve the declared SSPI Cargo dependency before packaging tests'
        source=sources[0]
    return load(source,'fixture_real_artifacts')

def synthetic_handoff(monkeypatch,backend,real_artifacts,tmp_path,barrier=None):
    import threading
    import zipfile
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv('CARGO_TARGET_DIR',str(tmp_path/'shared-cache'))
    monkeypatch.setattr(backend,'settings',lambda _: (['--locked'],'synthetic','release',[],{}))
    class Producer:
        def identify(self,*args,**kwargs):
            fingerprint=threading.current_thread().name.encode().hex().ljust(64,'0')[:64]
            return fingerprint,tmp_path/'Cargo.toml',{'target':'synthetic'}
        bundle=staticmethod(real_artifacts.bundle)
        validate_wheel=staticmethod(real_artifacts.validate_wheel) if hasattr(real_artifacts,'validate_wheel') else None
    monkeypatch.setattr(backend,'producer',lambda *args:Producer())
    def run(command,*,env,check,stdout=None):
        fingerprint=env['GWZ_SSPI_BUILD_FINGERPRINT'];target=Path(env['CARGO_TARGET_DIR'])
        if command[0]=='cargo':
            worker=target/'synthetic/release/gwz-sspi-worker';worker.parent.mkdir(parents=True,exist_ok=True);worker.write_text(fingerprint)
            return subprocess.CompletedProcess(command,0)
        wheel=target/'wheels/gwz-1-cp310-abi3-test.whl';wheel.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(wheel,'w') as archive:
            archive.writestr('gwz/_gwz_core.abi3.so',fingerprint)
            archive.writestr('gwz-1.dist-info/RECORD','')
        if barrier:barrier.wait(timeout=10)
        return subprocess.CompletedProcess(command,0,stdout=(str(wheel)+'\n').encode())
    monkeypatch.setattr(backend.subprocess,'run',run)

def coherent_wheel(path):
    import base64,csv,hashlib,io,zipfile
    with zipfile.ZipFile(path) as archive:
        fingerprint=json.loads(archive.read('gwz/sspi-artifact-set.json'))['build_fingerprint']
        assert archive.read('gwz/_gwz_core.abi3.so')==fingerprint.encode()
        assert archive.read('gwz/gwz-sspi-worker')==fingerprint.encode()
        rows=list(csv.reader(io.StringIO(archive.read('gwz-1.dist-info/RECORD').decode())))
        assert {row[0] for row in rows}==set(archive.namelist())
        for name,digest,size in rows:
            if digest:
                data=archive.read(name)
                assert digest=='sha256='+base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
                assert size==str(len(data))
        return fingerprint

@pytest.mark.parametrize('selection',['explicit','candidate-default','backend-default'])
def test_candidate_and_backend_preserve_owned_scratch_root(tmp_path,monkeypatch,backend,real_artifacts,selection):
    synthetic_handoff(monkeypatch,backend,real_artifacts,tmp_path)
    run=backend.subprocess.run;targets=[]
    def observed(command,**kwargs):
        targets.append(Path(kwargs['env']['CARGO_TARGET_DIR']))
        return run(command,**kwargs)
    monkeypatch.setattr(backend.subprocess,'run',observed)
    destination=tmp_path/'candidate';destination.mkdir()
    if selection=='backend-default':
        output=destination/'wheels'
        expected=output
        monkeypatch.delenv('CARGO_TARGET_DIR',raising=False)
        wheel=output/backend.build_wheel(str(output))
    else:
        candidate=load(ROOT/'scripts/build_candidate_extension.py','scratch_candidate_recipe')
        prepared=candidate.Prepared(destination,tmp_path/'core',destination/'py/Cargo.toml')
        explicit=tmp_path/'another-volume' if selection=='explicit' else None
        expected=explicit or destination/'target'
        def handoff(command,*,env,**kwargs):
            assert env['CARGO_TARGET_DIR']==str(expected)
            with monkeypatch.context() as invocation:
                invocation.setenv('CARGO_TARGET_DIR',env['CARGO_TARGET_DIR'])
                backend.build_wheel(str(prepared.wheels))
        wheel=candidate.build(prepared,target_dir=explicit,run=handoff)
    assert len(targets)==2 and targets[0]!=targets[1]
    assert all(target.is_relative_to(expected) and target!=expected for target in targets)
    assert all(not target.exists() for target in targets),'all build-owned scratch is disposed'
    assert not list(expected.glob('gwz-sspi-target-*'))
    assert not list(wheel.parent.glob('.gwz-sspi-build-*'))
    coherent_wheel(wheel)

@pytest.mark.parametrize('same_output',[False,True])
def test_real_handoff_and_bundler_isolate_same_name_builds(tmp_path,monkeypatch,backend,real_artifacts,same_output):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    barrier=threading.Barrier(2)
    synthetic_handoff(monkeypatch,backend,real_artifacts,tmp_path,barrier)
    def build(index):
        output=tmp_path/('out' if same_output else 'out'+str(index));output.mkdir(exist_ok=True)
        try:
            name=backend.build_wheel(str(output))
            return output/name,threading.current_thread().name.encode().hex().ljust(64,'0')[:64]
        except RuntimeError as error:
            assert same_output and 'collision' in str(error)
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(build,range(2)))
    successes=[result for result in results if result]
    assert len(successes)==(1 if same_output else 2)
    for path,expected in successes:assert coherent_wheel(path)==expected
    assert not list(tmp_path.rglob('*.tmp'))

@pytest.mark.parametrize('prior',[False,True])
@pytest.mark.parametrize('fault',['capture','provision'])
def test_failed_real_handoff_never_replaces_complete_destination(tmp_path,monkeypatch,backend,real_artifacts,prior,fault):
    import shutil,zipfile
    synthetic_handoff(monkeypatch,backend,real_artifacts,tmp_path)
    output=tmp_path/'out';output.mkdir();destination=output/'gwz-1-cp310-abi3-test.whl'
    if prior:
        backend.build_wheel(str(output));saved=destination.read_bytes();coherent_wheel(destination)
    if fault=='capture':
        def failed(source,target):Path(target).write_bytes(b'partial capture');raise OSError('forced capture')
        monkeypatch.setattr(shutil,'copyfile',failed)
    else:
        original=zipfile.ZipFile.writestr
        def failed(archive,*args,**kwargs):
            if str(archive.filename).endswith('.tmp'):raise OSError('forced provisioning')
            return original(archive,*args,**kwargs)
        monkeypatch.setattr(zipfile.ZipFile,'writestr',failed)
    with pytest.raises(OSError,match='forced'):backend.build_wheel(str(output))
    if prior:assert destination.read_bytes()==saved
    else:assert not destination.exists()
    assert not list((tmp_path/'shared-cache').iterdir())


@pytest.mark.parametrize('arguments,expected_target,expected_interpreter',[
    ('','i686-pc-windows-msvc',None),
    ('--target x86_64-pc-windows-msvc','x86_64-pc-windows-msvc',None),
    ('-i /explicit/python','i686-pc-windows-msvc','/explicit/python'),
    ('--target=custom-target --interpreter=/explicit/python','custom-target','/explicit/python'),
])
def test_metadata_worker_and_extension_use_one_normalized_frontend_choice(tmp_path,monkeypatch,backend,real_artifacts,arguments,expected_target,expected_interpreter):
    monkeypatch.setattr(backend.maturin,'get_config',lambda:{})
    monkeypatch.setattr(backend,'interpreter_facts',lambda _:('Windows','AMD64',32))
    monkeypatch.setattr(backend.subprocess,'check_output',lambda *a,**k:'host: x86_64-pc-windows-msvc\n')
    monkeypatch.delenv('CARGO_BUILD_TARGET',raising=False)
    config={'maturin.build-args':arguments};args,target,_,_,_=backend.settings(config)
    assert target==expected_target
    def option(command,name):
        for index,item in enumerate(command):
            if item==name:return command[index+1]
            if item.startswith(name+'='):return item.split('=',1)[1]
            if name=='--interpreter' and item=='-i':return command[index+1]
    assert option(args,'--interpreter')==(expected_interpreter or sys.executable)
    calls=[]
    def metadata(command,**kwargs):calls.append(command);return subprocess.CompletedProcess(command,0,stdout=b'gwz-1.dist-info\n')
    monkeypatch.setattr(backend.subprocess,'run',metadata)
    assert backend.prepare_metadata_for_build_wheel(str(tmp_path),config)=='gwz-1.dist-info'
    assert calls[0][-len(args):]==args
    assert option(calls[0],'--target')==target
    class Producer:
        def identify(self,manifest,**inputs):
            assert inputs['target']==target
            assert inputs['options']['maturin_args']==args
            return '42'*32,tmp_path/'Cargo.toml',inputs
        bundle=staticmethod(real_artifacts.bundle)
    monkeypatch.setattr(backend,'producer',lambda *args:Producer())
    import zipfile
    def build(command,*,env,check,stdout=None):
        calls.append(command)
        assert option(command,'--target')==target
        if command[0]=='cargo':
            name='gwz-sspi-worker.exe' if 'windows' in target else 'gwz-sspi-worker'
            binary=Path(env['CARGO_TARGET_DIR'])/target/'release'/name;binary.parent.mkdir(parents=True);binary.write_bytes(b'worker')
            return subprocess.CompletedProcess(command,0)
        assert option(command,'--interpreter')==(expected_interpreter or sys.executable)
        wheel=Path(env['CARGO_TARGET_DIR'])/'gwz-1-cp310-abi3-test.whl';wheel.parent.mkdir(parents=True)
        with zipfile.ZipFile(wheel,'w') as archive:
            archive.writestr('gwz/_gwz_core.abi3.so','extension');archive.writestr('gwz-1.dist-info/RECORD','')
        return subprocess.CompletedProcess(command,0,stdout=(str(wheel)+'\n').encode())
    monkeypatch.setattr(backend.subprocess,'run',build)
    backend.build_wheel(str(tmp_path),config)
    assert len(calls)==3

@pytest.mark.skipif(sys.platform=='win32',reason='the PATH maturin fixture is a POSIX script')
def test_hooks_run_the_maturin_on_path_as_maturins_own_backend_does(tmp_path,monkeypatch,backend):
    # pip's build isolation installs maturin into an overlay that it puts on PATH,
    # not beside sys.executable, so `python -m maturin` finds no maturin script.
    import os
    isolated=tmp_path/'isolated-bin';isolated.mkdir();log=tmp_path/'calls.jsonl';wheel=tmp_path/'gwz-1-cp310-abi3-test.whl'
    fake=isolated/'maturin'
    fake.write_text(f"#!{sys.executable}\nimport json,sys\nopen({str(log)!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"if sys.argv[2]=='build-wheel':\n    open({str(wheel)!r},'wb').close();print({str(wheel)!r})\nelse:\n    print('gwz-1.dist-info')\n")
    fake.chmod(0o755)
    monkeypatch.setenv('PATH',str(isolated)+os.pathsep+os.environ.get('PATH',''))
    monkeypatch.setattr(backend,'settings',lambda _:(['--locked'],None,None,None,{}))
    assert backend.prepare_metadata_for_build_wheel(str(tmp_path))=='gwz-1.dist-info'
    out=tmp_path/'out';out.mkdir()
    assert backend.maturin_wheel(str(out),['--locked'],dict(os.environ),editable=True)==wheel.name
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    assert [call[:2] for call in calls]==[['pep517','write-dist-info'],['pep517','build-wheel']]

def test_direct_run_uses_the_maturin_installed_with_its_interpreter(tmp_path,monkeypatch,backend):
    # package_smoke.py and build_candidate_extension.py run the backend with a
    # venv's python, whose maturin need not be the one PATH names.
    import sysconfig
    scripts=tmp_path/'scripts';scripts.mkdir()
    installed=scripts/('maturin.exe' if sys.platform=='win32' else 'maturin');installed.write_bytes(b'');installed.chmod(0o755)
    original=sysconfig.get_path
    monkeypatch.setattr(sysconfig,'get_path',lambda name,*args,**kwargs:str(scripts) if name=='scripts' else original(name,*args,**kwargs))
    calls=[]
    monkeypatch.setattr(backend,'build_wheel',lambda out,config,*,maturin:calls.append(maturin) or 'gwz-1.whl')
    monkeypatch.setattr(sys,'argv',['sspi_backend.py','--out',str(tmp_path/'out')])
    backend.main()
    assert len(calls)==1 and Path(calls[0]).parent==scripts and Path(calls[0]).stem.lower()=='maturin'

@pytest.mark.parametrize('failure',[OSError('unsupported hard links'),KeyboardInterrupt()])
def test_publication_failure_keeps_prior_complete_artifact(tmp_path,monkeypatch,backend,real_artifacts,failure):
    synthetic_handoff(monkeypatch,backend,real_artifacts,tmp_path)
    output=tmp_path/'out';output.mkdir()
    def refuse(*args):raise failure
    monkeypatch.setattr(backend.os,'link',refuse)
    with pytest.raises(type(failure)):backend.build_wheel(str(output))
    assert not list(output.glob('*.whl'))
    assert not list(output.iterdir())

def test_real_cli_matrix_receipts_survive_flattened_aggregation(tmp_path,monkeypatch):
    import shutil
    source=ROOT.parent/'gwz-cli/scripts/build_sspi.py'
    if not source.is_file():pytest.skip('cross-host recipe row requires the public CLI source')
    wrapper=load(source,'fixture_cli_wrapper')
    class Producer:
        def identify(self,manifest,**inputs):return inputs['target'].encode().hex().ljust(64,'0'),manifest,inputs
        def receipt(self,identifier,inputs):return json.dumps({'build_fingerprint':identifier,'inputs':inputs})
    monkeypatch.setattr(wrapper,'producer',lambda _:Producer())
    aggregate=tmp_path/'aggregate';aggregate.mkdir()
    for index,targets in enumerate([['one','two'],['three']]):
        root=tmp_path/str(index);root.mkdir()
        monkeypatch.setenv('GITHUB_ENV',str(root/'environment'))
        monkeypatch.setenv('CARGO_TARGET_DIR',str(root))
        monkeypatch.setattr(sys,'argv',['build_sspi.py','--targets',json.dumps(targets),'--dist-args=--artifacts=local'])
        wrapper.main()
        for receipt in (root/'distrib').glob('sspi-artifact-sets*.json'):shutil.copyfile(receipt,aggregate/receipt.name)
    records=[record for receipt in aggregate.glob('*.json') for record in json.loads(receipt.read_text())]
    assert sorted(record['inputs']['target'] for record in records)==['one','three','two']
    assert len({record['build_fingerprint'] for record in records})==3
