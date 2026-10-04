"""PEP517 wheel backend: provision matching extension/worker, then maturin.

No runtime activation. Source distributions carry this backend and the Cargo
resolved dependencies; receipts do not replace the compiled Hello trust check.
"""
import importlib.util
import json
import os
from pathlib import Path
import platform
import struct
import sys
import subprocess
import tempfile
import maturin

def producer(manifest,build_args=()):
    command=['cargo','metadata','--locked','--format-version','1','--manifest-path',str(manifest)]
    if any(flag in build_args for flag in ('--offline','--frozen')):command+=['--offline']
    metadata=json.loads(subprocess.check_output(command))
    workers=[p for p in metadata['packages'] if p['name']=='gwz-sspi']
    if len(workers)!=1:raise RuntimeError('ambiguous or absent SSPI dependency')
    path=Path(workers[0]['manifest_path']).parent/'scripts/artifact_set.py'
    spec=importlib.util.spec_from_file_location('artifact_set',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def settings(config_settings):
    args=maturin.get_maturin_pep517_args(config_settings)
    config=maturin.get_config()
    flags={'--release','--locked','--offline','--frozen','--strip','--no-default-features','-v','-q','--verbose','--quiet'}
    values={'--target','--profile','--features','-F','--auditwheel','--compatibility','--manylinux','--interpreter','-i'}
    parsed={};index=0
    while index<len(args):
        item=args[index];key,separator,value=item.partition('=')
        key={'-F':'--features','-i':'--interpreter'}.get(key,key)
        if key in values:
            if not separator:
                index+=1
                if index==len(args):raise RuntimeError('missing packaging option value')
                value=args[index]
            if key in parsed:raise RuntimeError('duplicate packaging option')
            parsed[key]=value
        elif item not in flags:raise RuntimeError('unsupported packaging option')
        index+=1
    compiler=subprocess.check_output(['rustc','-vV'],text=True)
    interpreter=parsed.get('--interpreter',sys.executable)
    if '--interpreter' not in parsed and (os.environ.get('MATURIN_PEP517_USE_BASE_PYTHON') in ('1','true') or config.get('use-base-python')):
        base=getattr(sys,'_base_executable',None)
        if base and Path(base).is_file():interpreter=str(Path(base).resolve())
    system,machine,width=interpreter_facts(interpreter)
    default_target='i686-pc-windows-msvc' if system.lower()=='windows' and machine.lower()=='amd64' and width==32 else next(line[6:] for line in compiler.splitlines() if line.startswith('host: '))
    target=parsed.get('--target') or config.get('target') or os.environ.get('CARGO_BUILD_TARGET') or default_target
    if not target.isascii() or not target or not all(c.isalnum() or c in '-_' for c in target):raise RuntimeError('target must be a Rust triple, not a JSON path')
    profile=parsed.get('--profile',config.get('profile','release'))
    if '--release' in args:
        if '--profile' in parsed and profile!='release':raise RuntimeError('conflicting packaging profiles')
        profile='release'
    if profile not in ('dev','release'):raise RuntimeError('unsupported worker build profile')
    features=list(config.get('features',[]))+parsed.get('--features',parsed.get('-F','')).replace(' ',',').split(',')
    features=sorted(set(filter(None,features)))
    # The effective profile/target is explicit for BOTH builds, even if a
    # frontend/maturin default changes. All delegated args/config are inputs.
    args=[arg for arg in args if arg!='--release']
    if '--profile' not in parsed:args+=['--profile',profile]
    if '--target' not in parsed:args+=['--target',target]
    if '--interpreter' not in parsed:args+=['--interpreter',interpreter]
    if '--locked' not in args:args+=['--locked']
    return args,target,profile,features,config

def interpreter_facts(interpreter):
    if interpreter==sys.executable:
        return platform.system(),platform.machine(),struct.calcsize('P')*8
    output=subprocess.check_output([interpreter,'-c',"import json,platform,struct;print(json.dumps([platform.system(),platform.machine(),struct.calcsize('P')*8]))"],text=True)
    facts=json.loads(output)
    if len(facts)!=3 or facts[2] not in (32,64):raise RuntimeError('unsupported build interpreter')
    return facts

def publish(staged,destination):
    # Closed, validated immutable file; same-filesystem atomic no-replace link.
    # Unsupported filesystems refuse. No stale lock or indefinite wait exists.
    try:os.link(staged,destination)
    except FileExistsError as error:raise RuntimeError('packaging output collision') from error

# The PEP517 hooks run maturin as maturin's own backend does, from PATH: pip's
# build isolation installs it into an overlay on PATH, not beside
# sys.executable, where `python -m maturin` looks for it. A direct run (main)
# names the maturin installed with its interpreter instead.
def maturin_wheel(wheel_directory,args,environment,metadata_directory=None,editable=False,maturin='maturin'):
    import shutil
    command=[maturin,'pep517','build-wheel',*args]
    if editable:command+=['--editable']
    if metadata_directory:environment={**environment,'MATURIN_PEP517_METADATA_DIR':metadata_directory}
    result=subprocess.run(command,env=environment,check=True,stdout=subprocess.PIPE)
    path=Path(result.stdout.decode().strip().splitlines()[-1])
    if not editable and not path.resolve().is_relative_to(Path(environment['CARGO_TARGET_DIR']).resolve()):raise RuntimeError('raw wheel escaped owned build output')
    destination=Path(wheel_directory)/path.name
    if path.resolve()!=destination.resolve():shutil.copyfile(path,destination)
    return path.name

def build_wheel(wheel_directory,config_settings=None,metadata_directory=None,*,maturin='maturin'):
    args,target,profile,features,config=settings(config_settings)
    manifest=Path(config.get('manifest-path','Cargo.toml')).resolve();module=producer(manifest,args)
    identifier,worker,inputs=module.identify(manifest,target=target,profile=profile,features=features,options={'maturin_args':args,'maturin_config':config})
    output=Path(wheel_directory).resolve();output.mkdir(parents=True,exist_ok=True)
    scratch_root=Path(os.environ.get('CARGO_TARGET_DIR',output)).resolve();scratch_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gwz-sspi-build-',dir=output) as temporary, tempfile.TemporaryDirectory(prefix='gwz-sspi-target-',dir=scratch_root) as scratch:
        environment={**os.environ,'GWZ_SSPI_BUILD_FINGERPRINT':identifier}
        # The extension target and returned raw wheel are build-owned too.
        # CARGO_TARGET_DIR selects the root, never a shared mutable target tree.
        environment['CARGO_TARGET_DIR']=str(Path(scratch)/'extension-target')
        command=['cargo','build','--locked','--manifest-path',str(worker),'--features','worker-bin','--target',target,'--profile',profile]
        command += [arg for arg in args if arg in ('--offline','--frozen')]
        # Worker output is build-owned through Cargo completion and insertion.
        worker_environment={**environment,'CARGO_TARGET_DIR':str(Path(scratch)/identifier/'worker')}
        subprocess.run(command,env=worker_environment,check=True)
        name='gwz-sspi-worker.exe' if 'windows' in target else 'gwz-sspi-worker'
        binary=Path(worker_environment['CARGO_TARGET_DIR']).resolve()/target/('debug' if profile=='dev' else profile)/name
        filename=maturin_wheel(temporary,args,environment,metadata_directory,maturin=maturin)
        staged=Path(temporary)/filename
        module.bundle(staged,binary,identifier,inputs)
        publish(staged,output/filename)
        return filename

def build_editable(wheel_directory,config_settings=None,metadata_directory=None):
    # Existing development installs remain supported, explicitly unprovisioned.
    # Maturin places their extension in the source tree; no packaged worker is
    # installed there and no packaging fingerprint may activate that image.
    environment=dict(os.environ)
    environment.pop('GWZ_SSPI_BUILD_FINGERPRINT',None)
    environment.pop('GWZ_SSPI_PACKAGING_TARGETS',None)
    args,_,_,_,_=settings(config_settings)
    with tempfile.TemporaryDirectory(prefix='gwz-editable-build-') as temporary:
        environment.setdefault('CARGO_TARGET_DIR',str(Path(temporary)/'target'))
        return maturin_wheel(wheel_directory,args,environment,metadata_directory,editable=True)

get_requires_for_build_editable=maturin.get_requires_for_build_editable
def prepare_metadata_for_build_wheel(metadata_directory,config_settings=None):
    args,_,_,_,_=settings(config_settings)
    result=subprocess.run(['maturin','pep517','write-dist-info','--metadata-directory',metadata_directory,*args],env=dict(os.environ),check=True,stdout=subprocess.PIPE)
    return result.stdout.decode().strip().splitlines()[-1]

prepare_metadata_for_build_editable=prepare_metadata_for_build_wheel
# Metadata/sdist do not execute worker code. Maturin vendors local Cargo path
# dependencies in the sdist; producer discovery uses the extracted Cargo graph.
get_requires_for_build_wheel=maturin.get_requires_for_build_wheel
def build_sdist(sdist_directory,config_settings=None):
    # Maturin relocates path-dependent crates under the archive root. Keep the
    # PEP517 backend at the root backend-path rather than relying on siblings.
    import io,tarfile
    output=Path(sdist_directory).resolve();output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.gwz-sdist-',dir=output) as temporary:
        filename=maturin.build_sdist(temporary,config_settings)
        path=Path(temporary)/filename
        descriptor,name=tempfile.mkstemp(suffix='.tmp',dir=temporary);os.close(descriptor)
        rewritten=Path(name)
        with tarfile.open(path,'r:gz') as source,tarfile.open(rewritten,'w:gz') as archive:
            members=source.getmembers();root=members[0].name.split('/')[0]
            backend=root+'/build_support/sspi_backend.py'
            for member in members:
                if member.name!=backend:archive.addfile(member,source.extractfile(member) if member.isfile() else None)
            data=Path(__file__).read_bytes()
            member=tarfile.TarInfo(backend);member.size=len(data);member.mode=0o644
            archive.addfile(member,io.BytesIO(data))
        with tarfile.open(rewritten,'r:gz') as archive:
            if archive.extractfile(backend).read()!=data:raise RuntimeError('incomplete source backend')
        publish(rewritten,output/filename)
        return filename

def interpreter_maturin():
    # The maturin installed with this interpreter, the one `python -m maturin`
    # ran when package_smoke.py and build_candidate_extension.py called it.
    import shutil,sysconfig
    found=shutil.which('maturin',path=sysconfig.get_path('scripts'))
    if found is None:raise RuntimeError('maturin is not installed with this interpreter')
    return found

def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True,help='wheel output directory; same-name publication refuses');parser.add_argument('--build-args',default='',help='delegated maturin options; default frontend interpreter/native target/release profile')
    args=parser.parse_args();Path(args.out).mkdir(parents=True,exist_ok=True)
    print(build_wheel(args.out,{'maturin.build-args':args.build_args},maturin=interpreter_maturin()))
if __name__=='__main__':main()
