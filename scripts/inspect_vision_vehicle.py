"""Read-only resource/runtime inventory through the existing pinned SSH connection."""
import json
from pathlib import Path
import stage_avoidance_release as remote

if __name__ == '__main__':
    client = remote.connect('192.168.1.106')
    try:
        source = '''import json,shutil,subprocess,urllib.request,importlib.metadata
from pathlib import Path
root=Path('/home/jetson/ros2-smart-car-avoidance-20261028-66c66971')
versions={}
for name in ['torch','ultralytics','numpy','opencv-python']:
 try:versions[name]=importlib.metadata.version(name)
 except importlib.metadata.PackageNotFoundError:versions[name]=None
try:status=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status',timeout=4))
except Exception as exc:status={'error':str(exc)}
print(json.dumps({'disk':shutil.disk_usage(root)._asdict(),'memory':Path('/proc/meminfo').read_text().splitlines()[:4],
'versions':versions,'status':status,'driver':str(Path('/home/jetson/insightrover-full-scan-20261028/src/sllidar_ros2/src/sllidar_node.cpp')),
'gpu':subprocess.run(['cat','/etc/nv_tegra_release'],capture_output=True,text=True).stdout}))
'''
        result = remote.python(client, source)
        target = Path(__file__).resolve().parents[1] / 'output/vision-search-20261028'
        target.mkdir(parents=True, exist_ok=True)
        (target / 'vehicle-inventory.json').write_text(result, encoding='utf-8')
        value = json.loads(result)
        print(json.dumps({k:v for k,v in value.items() if k != 'status'},ensure_ascii=False))
        print(json.dumps({'mode':value['status'].get('mode'),'avoidance':value['status'].get('avoidance')},ensure_ascii=False))
    finally:
        client.close()
