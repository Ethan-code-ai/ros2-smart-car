"""Stage the avoidance release through the project's existing pinned SSH helper."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OLD = '/home/jetson/ros2-smart-car-avoidance-20261028-b83278d4'


def connect(host):
    helper = ROOT.parent/'output/system-test-20261008/car_deploy.py'
    spec = importlib.util.spec_from_file_location('existing_vehicle_connection', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HOST = host
    return module.connect()


def run(client, command, timeout=30):
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output, error = stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')
    code = stdout.channel.recv_exit_status()
    if code:
        raise RuntimeError(f'Remote command failed ({code}): {output[-3000:]} {error[-3000:]}')
    return output


def python(client, source, timeout=30):
    return run(client, 'python3 -c ' + shlex.quote(source), timeout)


def inspect(client):
    source = f'''import json,shutil,socket,subprocess,hashlib
from pathlib import Path
import yaml
root=Path({OLD!r})
entry=root/'start_test.sh'
config=root/'ros2_ws/src/smart_car_decision/config/decision.yaml'
settings=yaml.safe_load(config.read_text())
web=settings['web_app_node']['ros__parameters']
unit=subprocess.run(['systemctl','--user','show','smart-car-mapping-settings-20261020.service','-p','ActiveState','-p','ExecStart','-p','WorkingDirectory'],capture_output=True,text=True)
print(json.dumps({{'hostname':socket.gethostname(),'root':str(root),'free_bytes':shutil.disk_usage(root).free,
 'entry':entry.read_text(),'entry_sha256':hashlib.sha256(entry.read_bytes()).hexdigest(),
 'config_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),'controller':settings.get('decision_controller'),
 'web':{{k:v for k,v in web.items() if k in ['static_dir','camera_source','navigation_robot_radius','navigation_config_path','motion_mapping_settings_path']}},
 'unit':unit.stdout}}))
'''
    return json.loads(python(client, source))


def stage(client, profile):
    output = ROOT/'output/active-avoidance-20261028-v037'
    release = json.loads((output/'release.json').read_text(encoding='utf-8'))
    archive = output/release['archive']
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != release['sha256']:
        raise RuntimeError('Local release checksum changed')
    with tarfile.open(archive) as package:
        manifest = json.load(package.extractfile('source-sha256.json'))
        for name, expected in manifest.items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != expected:
                raise RuntimeError('Workspace differs from tested release: ' + name)
    target = '/home/jetson/ros2-smart-car-avoidance-20261028-' + digest[:8]
    if profile['free_bytes'] < archive.stat().st_size*3 + 200*1024*1024:
        raise RuntimeError('Insufficient free disk space; no files uploaded')
    source = f'''from pathlib import Path
import json
target=Path({target!r})
assert target.parent.resolve()==Path('/home/jetson').resolve()
assert not target.exists(), 'Release target already exists; refusing overwrite'
target.mkdir(mode=0o755)
print(json.dumps({{'target':str(target)}}))
'''
    print(python(client, source), flush=True)
    sftp = client.open_sftp()
    try:
        sftp.put(str(archive), target+'/release.tar.gz')
    finally:
        sftp.close()
    print('Archive uploaded; verifying source files and preserving vehicle configuration.', flush=True)
    source = f'''import copy,hashlib,json,shutil,tarfile
from pathlib import Path
import yaml
root=Path({target!r}); old=Path({OLD!r}); archive=root/'release.tar.gz'
assert hashlib.sha256(archive.read_bytes()).hexdigest()=={digest!r}, 'Upload hash mismatch'
with tarfile.open(archive) as package:
 members=package.getmembers()
 assert len(members)<5000
 for item in members:
  assert (item.isfile() or item.isdir()) and not item.issym() and not item.islnk()
  assert not Path(item.name).is_absolute() and (root/item.name).resolve().is_relative_to(root.resolve())
 package.extractall(root)
manifest=json.loads((root/'source-sha256.json').read_text())
for name,expected in manifest.items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected, name
config_relative=Path('ros2_ws/src/smart_car_decision/config')
preserved={{}}
for path in (old/config_relative).glob('*.yaml'):
 destination=root/config_relative/path.name
 if destination.is_file():
  shutil.copy2(path,destination)
  preserved[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
config=root/config_relative/'decision.yaml'
settings=yaml.safe_load(config.read_text())
settings['mode_manager']['ros__parameters']['initial_mode']='stop'
web=settings['web_app_node']['ros__parameters']
web['static_dir']=str(root/'web-console/dist')
# Copy release-private state files without redirecting shared ~/.ros settings.
for key in ('navigation_config_path','motion_mapping_settings_path','avoidance_settings_path'):
 value=web.get(key)
 if isinstance(value,str) and value.startswith(str(old)+'/'):
  relative=Path(value).relative_to(old)
  destination=root/relative
  assert destination.resolve().is_relative_to(root.resolve())
  if Path(value).is_file():
   destination.parent.mkdir(parents=True,exist_ok=True)
   shutil.copy2(value,destination)
  web[key]=str(destination)
config.write_text(yaml.safe_dump(settings,allow_unicode=True,sort_keys=False))
entry=(old/'start_test.sh').read_text().replace(str(old),str(root))
assert str(old) not in entry and str(root/'ros2_ws/install/setup.bash') in entry
(root/'start_test.sh').write_text(entry)
(root/'start_test.sh').chmod(0o755)
(root/'logs').mkdir()
effective={{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in manifest}}
record={{'status':'uploaded_not_built','archive_sha256':{digest!r},'source_files_verified':len(manifest),
 'previous_deployment':str(old),'target':str(root),'preserved_config_hashes':preserved,
 'effective_source_hashes':effective,'startup_sha256':hashlib.sha256((root/'start_test.sh').read_bytes()).hexdigest(),
 'old_entry_unchanged':hashlib.sha256((old/'start_test.sh').read_bytes()).hexdigest()=={profile['entry_sha256']!r},
 'old_config_unchanged':hashlib.sha256((old/config_relative/'decision.yaml').read_bytes()).hexdigest()=={profile['config_sha256']!r},
 'activated':False,'motion_commands_sent':False}}
assert record['old_entry_unchanged'] and record['old_config_unchanged']
(root/'deployment.json').write_text(json.dumps(record,indent=2))
print(json.dumps({{k:v for k,v in record.items() if k!='effective_source_hashes'}},indent=2))
'''
    prepared = json.loads(python(client, source, timeout=60))
    (output/'vehicle-prepared.json').write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Source verified; building isolated ROS package with symlink install.', flush=True)
    # Reuse only the inspected setup commands; never execute the old launch entry.
    setups = [line for line in profile['entry'].splitlines() if line.startswith('source ') and line.endswith('/setup.bash')]
    command = '\n'.join(['set -eo pipefail', 'unset AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH PYTHONPATH',
                         *setups, 'cd '+shlex.quote(target+'/ros2_ws'),
                         'colcon build --symlink-install --packages-select smart_car_decision --event-handlers console_direct+ > '+shlex.quote(target+'/logs/build.log')+' 2>&1',
                         'cat '+shlex.quote(target+'/logs/build.log'),
                         'source '+shlex.quote(target+'/ros2_ws/install/setup.bash'),
                         'python3 -c '+shlex.quote("import smart_car_decision.avoidance as a; import smart_car_decision.decision_controller as c; import smart_car_decision.web_app_node as w; from ament_index_python.packages import get_package_share_directory; from pathlib import Path; root=Path("+repr(target)+"); assert all(Path(m.__file__).resolve().is_relative_to(root) for m in (a,c,w)); assert Path(get_package_share_directory('smart_car_decision')).resolve().is_relative_to(root); print('New installed imports verified')")])
    try:
        build_log = run(client, 'bash --noprofile --norc -c '+shlex.quote(command), timeout=180)
    except Exception:
        log = run(client, 'tail -n 80 '+shlex.quote(target+'/logs/build.log'), timeout=10)
        (output/'vehicle-build.log').write_text(log, encoding='utf-8')
        raise
    (output/'vehicle-build.log').write_text(build_log, encoding='utf-8')
    print(build_log, flush=True)
    return verify(client, profile, target, digest)


def verify(client, profile, target, digest):
    source = f'''import hashlib,json,shutil
from pathlib import Path
root=Path({target!r}); old=Path({OLD!r})
record=json.loads((root/'deployment.json').read_text())
assert record['archive_sha256']=={digest!r}
assert hashlib.sha256((root/'release.tar.gz').read_bytes()).hexdigest()=={digest!r}
assert record['preserved_config_hashes']['decision.yaml']=={profile['config_sha256']!r}
assert hashlib.sha256((root/'start_test.sh').read_text().replace(str(root),str(old)).encode()).hexdigest()=={profile['entry_sha256']!r}
for name,expected in record['effective_source_hashes'].items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
package=root/'ros2_ws/install/smart_car_decision'
source=root/'ros2_ws/src/smart_car_decision'
links=list(package.glob('lib/python*/site-packages/*.egg-link'))
assert len(links)==1, 'Expected exactly one symlink-install package link'
linked=Path(links[0].read_text().splitlines()[0])
if not linked.is_absolute(): linked=links[0].parent/linked
modules=linked/'smart_car_decision'
assert modules.resolve().is_relative_to(root.resolve()), 'Install resolves outside new release'
for path in (source/'smart_car_decision').glob('*.py'):
 assert hashlib.sha256((modules/path.name).read_bytes()).hexdigest()==hashlib.sha256(path.read_bytes()).hexdigest(),path.name
for path in (source/'config').glob('*.yaml'):
 installed=package/'share/smart_car_decision/config'/path.name
 assert hashlib.sha256(installed.read_bytes()).hexdigest()==hashlib.sha256(path.read_bytes()).hexdigest(),path.name
assert hashlib.sha256((old/'start_test.sh').read_bytes()).hexdigest()=={profile['entry_sha256']!r}
assert hashlib.sha256((old/'ros2_ws/src/smart_car_decision/config/decision.yaml').read_bytes()).hexdigest()=={profile['config_sha256']!r}
record.update(status='built_verified_not_activated',installed_modules_verified=True,installed_config_verified=True,
 free_bytes_after=shutil.disk_usage(root).free)
assert record['free_bytes_after']>200*1024*1024
(root/'deployment.json').write_text(json.dumps(record,indent=2))
print(json.dumps({{k:v for k,v in record.items() if k!='effective_source_hashes'}},indent=2))
'''
    result = json.loads(python(client, source))
    (ROOT/'output/active-avoidance-20261028-v037/vehicle-staging.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='192.168.1.106')
    parser.add_argument('--stage', action='store_true')
    parser.add_argument('--verify', action='store_true', help='resume verification of the uploaded and built release')
    args = parser.parse_args()
    client = connect(args.host)
    try:
        profile = inspect(client)
        if args.stage:
            result = stage(client, profile)
        elif args.verify:
            release = json.loads((ROOT/'output/active-avoidance-20261028-v037/release.json').read_text(encoding='utf-8'))
            digest = release['sha256']
            result = verify(client, profile, '/home/jetson/ros2-smart-car-avoidance-20261028-'+digest[:8], digest)
        else:
            result = profile
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == '__main__':
    main()
