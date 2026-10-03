"""PEP517 wheel backend: provision matching extension/worker, then maturin.

No runtime activation. Source distributions carry this backend and the Cargo
resolved dependencies; receipts do not replace the compiled Hello trust check.
"""
import importlib.util
import json
import os
from pathlib import Path
import shlex
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
    target=parsed.get('--target',config.get('target',os.environ.get('CARGO_BUILD_TARGET'))) or next(line[6:] for line in compiler.splitlines() if line.startswith('host: '))
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
    if '--locked' not in args:args+=['--locked']
    return args,target,profile,features,config

def maturin_wheel(wheel_directory,args,environment,metadata_directory=None,editable=False):
    import shutil
    command=[os.sys.executable,'-m','maturin','pep517','build-wheel',*args]
    if editable:command+=['--editable']
    if metadata_directory:environment={**environment,'MATURIN_PEP517_METADATA_DIR':metadata_directory}
    result=subprocess.run(command,env=environment,check=True,stdout=subprocess.PIPE)
    path=Path(result.stdout.decode().strip().splitlines()[-1]);destination=Path(wheel_directory)/path.name
    if path.resolve()!=destination.resolve():shutil.copyfile(path,destination)
    return path.name

def build_wheel(wheel_directory,config_settings=None,metadata_directory=None):
    args,target,profile,features,config=settings(config_settings)
    manifest=Path(config.get('manifest-path','Cargo.toml')).resolve();module=producer(manifest,args)
    identifier,worker,inputs=module.identify(manifest,target=target,profile=profile,features=features,options={'maturin_args':args,'maturin_config':config})
    with tempfile.TemporaryDirectory(prefix='gwz-sspi-build-') as temporary:
        environment={**os.environ,'GWZ_SSPI_BUILD_FINGERPRINT':identifier}
        environment.setdefault('CARGO_TARGET_DIR',str(Path(temporary)/'target'))
        command=['cargo','build','--locked','--manifest-path',str(worker),'--features','worker-bin','--target',target,'--profile',profile]
        command += [arg for arg in args if arg in ('--offline','--frozen')]
        # Worker output is build-owned: another wheel sharing the extension cache
        # cannot replace it between Cargo completion and wheel insertion.
        worker_environment={**environment,'CARGO_TARGET_DIR':str(Path(temporary)/identifier/'worker')}
        subprocess.run(command,env=worker_environment,check=True)
        name='gwz-sspi-worker.exe' if 'windows' in target else 'gwz-sspi-worker'
        binary=Path(worker_environment['CARGO_TARGET_DIR']).resolve()/target/('debug' if profile=='dev' else profile)/name
        filename=maturin_wheel(wheel_directory,args,environment,metadata_directory)
        module.bundle(Path(wheel_directory)/filename,binary,identifier,inputs)
        return filename

def build_editable(wheel_directory,config_settings=None,metadata_directory=None):
    # Existing development installs remain supported, explicitly unprovisioned.
    # Maturin places their extension in the source tree; no packaged worker is
    # installed there and no packaging fingerprint may activate that image.
    environment=dict(os.environ)
    environment.pop('GWZ_SSPI_BUILD_FINGERPRINT',None)
    environment.pop('GWZ_SSPI_PACKAGING_TARGETS',None)
    args=maturin.get_maturin_pep517_args(config_settings)
    with tempfile.TemporaryDirectory(prefix='gwz-editable-build-') as temporary:
        environment.setdefault('CARGO_TARGET_DIR',str(Path(temporary)/'target'))
        return maturin_wheel(wheel_directory,args,environment,metadata_directory,editable=True)

get_requires_for_build_editable=maturin.get_requires_for_build_editable
prepare_metadata_for_build_editable=maturin.prepare_metadata_for_build_editable
# Metadata/sdist do not execute worker code. Maturin vendors local Cargo path
# dependencies in the sdist; producer discovery uses the extracted Cargo graph.
get_requires_for_build_wheel=maturin.get_requires_for_build_wheel
prepare_metadata_for_build_wheel=maturin.prepare_metadata_for_build_wheel
def build_sdist(sdist_directory,config_settings=None):
    # Maturin relocates path-dependent crates under the archive root. Keep the
    # PEP517 backend at the root backend-path rather than relying on siblings.
    import io,tarfile
    filename=maturin.build_sdist(sdist_directory,config_settings)
    path=Path(sdist_directory)/filename;temporary=path.with_suffix('.tmp')
    try:
        with tarfile.open(path,'r:gz') as source,tarfile.open(temporary,'w:gz') as output:
            members=source.getmembers();root=members[0].name.split('/')[0]
            for member in members:output.addfile(member,source.extractfile(member) if member.isfile() else None)
            data=Path(__file__).read_bytes()
            member=tarfile.TarInfo(root+'/build_support/sspi_backend.py');member.size=len(data);member.mode=0o644
            output.addfile(member,io.BytesIO(data))
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
    return filename

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);parser.add_argument('--build-args',default='')
    args=parser.parse_args();Path(args.out).mkdir(parents=True,exist_ok=True)
    print(build_wheel(args.out,{'maturin.build-args':args.build_args}))
if __name__=='__main__':main()
