"""Build a private full-scan driver; preserve the vendor workspace unchanged."""
import json
from pathlib import Path
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
TARGET='/home/jetson/insightrover-full-scan-20261028'
client=remote.connect('192.168.1.106')
try:
    source=f'''from pathlib import Path
import shutil,hashlib,json
old=Path('/home/jetson/yahboomcar_ros2_ws/software/library_ws/src/sllidar_ros2')
root=Path({TARGET!r}); dest=root/'src/sllidar_ros2'
assert not root.exists(), 'driver target exists; inspect instead of overwrite'
assert shutil.disk_usage(old).free>100*1024*1024
shutil.copytree(old,dest)
p=dest/'src/sllidar_node.cpp'; text=p.read_text(); original=hashlib.sha256(text.encode()).hexdigest()
for index in ('i','node_count-1-i'):
 block=''' + repr('''                float angle = getAngle(nodes[i]);
                if (angle >= 90.0f && angle <= 270.0f) {
                    scan_msg->ranges[INDEX] = std::numeric_limits<float>::infinity();
                } else {
''') + f'''
 block=block.replace('INDEX',index)
 assert text.count(block)==1, index
 start=text.index(block); end=text.index('                scan_msg->intensities',start)
 segment=text[start+len(block):end]
 assert segment.endswith('                }}\\n')
 text=text[:start]+segment[:-18]+text[end:]
p.write_text(text)
record={{'source':str(old),'target':str(root),'original_sha256':original,'patched_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'change':'remove hardcoded 90..270 degree suppression; retain zero-return infinity'}}
(root/'driver-release.json').write_text(json.dumps(record,indent=2))
print(json.dumps(record))
'''
    record=json.loads(remote.python(client,source))
    log=remote.run(client,"bash -c 'source /opt/ros/humble/setup.bash; cd "+TARGET+"; colcon build --symlink-install --parallel-workers 1 --cmake-args -DBUILD_TESTING=OFF -DCMAKE_BUILD_TYPE=Release > build.log 2>&1; result=$?; tail -n 20 build.log; exit $result'",timeout=180)
    out=ROOT/'output/active-avoidance-20261028'
    (out/'driver-build.log').write_text(log,encoding='utf-8')
    (out/'driver-release.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(log)
finally:client.close()
