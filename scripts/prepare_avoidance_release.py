"""Produce a checksummed local review bundle; never connects to a vehicle."""
import hashlib
import json
import tarfile
from io import BytesIO
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    output = root/'output/active-avoidance-20261028-v037'
    output.mkdir(parents=True, exist_ok=True)
    if not (root/'web-console/dist/index.html').is_file():
        raise SystemExit('Build web-console before packaging')
    paths = [path for folder in (root/'ros2_ws/src/smart_car_decision', root/'web-console/dist')
             for path in folder.rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc']
    paths += [root/'desktop/insightrover_desktop/server.py', root/'docs/active-avoidance.md']
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    archive = output/'active-avoidance-release.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        for path in sorted(paths):
            package.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        data = json.dumps(hashes, indent=2).encode()
        entry = tarfile.TarInfo('source-sha256.json')
        entry.size = len(data)
        package.addfile(entry, BytesIO(data))
    manifest = {'status': 'local_bundle_not_deployed', 'archive': archive.name,
                'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'files': len(hashes),
                'vehicle_modified': False, 'vehicle_motion_tested': False,
                'defaults': {'enabled': True, 'trigger_distance_m': .6, 'speed_ratio': .8,
                             'max_speed_m_s': .25, 'max_angular_rad_s': .25,
                             'timeout_s': 15, 'distance_limit_m': 1.5},
                'desktop': 'Proxy source included; rebuild installed desktop app before using /api/avoidance there.'}
    (output/'release.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
