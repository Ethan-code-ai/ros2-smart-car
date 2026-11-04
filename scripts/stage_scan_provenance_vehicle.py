"""Build an isolated provenance driver. Does not switch a running driver."""
from pathlib import Path
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
TARGET='/home/jetson/insightrover-scan-provenance-20261028'
if __name__=='__main__':
    client=remote.connect('192.168.1.106')
    try:
        print(remote.python(client,f'''from pathlib import Path
import shutil
root=Path({TARGET!r});assert not root.exists(),'Already staged; inspect existing build'
assert shutil.disk_usage('/home/jetson').free>150*1024**2
source=Path('/home/jetson/insightrover-full-scan-20261028/src/sllidar_ros2')
dest=root/'src/sllidar_ros2';shutil.copytree(source,dest)
p=dest/'CMakeLists.txt';s=p.read_text();s=s.replace('find_package(sensor_msgs REQUIRED)','find_package(sensor_msgs REQUIRED)\\nfind_package(std_msgs REQUIRED)');s=s.replace('  sensor_msgs\\n','  sensor_msgs\\n  std_msgs\\n');p.write_text(s)
p=dest/'package.xml';s=p.read_text().replace('<depend>sensor_msgs</depend>','<depend>sensor_msgs</depend>\\n  <depend>std_msgs</depend>');p.write_text(s)
print('private source staged')
'''))
        with client.open_sftp() as s:s.put(str(ROOT/'output/vision-search-20261028/driver-provenance.cpp'),TARGET+'/src/sllidar_ros2/src/sllidar_node.cpp')
        print(remote.run(client,"bash -c 'source /opt/ros/humble/setup.bash; cd "+TARGET+"; colcon build --symlink-install --parallel-workers 1 --cmake-args -DBUILD_TESTING=OFF -DCMAKE_BUILD_TYPE=Release > build.log 2>&1; result=$?; tail -n 15 build.log; exit $result'",timeout=180))
    finally:client.close()
