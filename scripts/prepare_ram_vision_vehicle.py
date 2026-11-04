"""Explicit user-approved volatile dependency/model staging; no global pip changes."""
from pathlib import Path
import shlex
import stage_avoidance_release as remote

ROOT=Path(__file__).resolve().parents[1]
RAM='/dev/shm/insightrover-vision-20261028'
if __name__=='__main__':
    c=remote.connect('192.168.1.106')
    try:
        print(remote.python(c,f"from pathlib import Path\nimport shutil\np=Path({RAM!r});assert shutil.disk_usage('/dev/shm').free>2*1024**3\np.mkdir(exist_ok=True);(p/'tmp').mkdir(exist_ok=True);(p/'packages').mkdir(exist_ok=True)\nprint('volatile staging ready')"))
        with c.open_sftp() as s:s.put(str(ROOT/'scripts/prepare_yoloe11.py'),RAM+'/prepare_yoloe11.py')
        command=f"TMPDIR={RAM}/tmp PIP_NO_CACHE_DIR=1 python3 -m pip install --no-deps --target {RAM}/packages ultralytics==8.4.0 ftfy regex"
        print(remote.run(c,command,timeout=180))
        command=f"TMPDIR={RAM}/tmp PIP_NO_CACHE_DIR=1 python3 -m pip install --no-deps --target {RAM}/packages git+https://github.com/ultralytics/CLIP.git"
        print(remote.run(c,command,timeout=180))
        command=f"cd {RAM}; PYTHONPATH={RAM}/packages TMPDIR={RAM}/tmp PIP_NO_CACHE_DIR=1 python3 prepare_yoloe11.py --directory {RAM}/models"
        print(remote.run(c,command,timeout=240))
    finally:c.close()
