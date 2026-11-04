"""Verify frozen resources and create the versioned, data-free delivery folder."""
import hashlib
import json
import shutil
import struct
from pathlib import Path
import runpy

import pefile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'output/windows-app-20261028-v0311'


def main():
    version = runpy.run_path(str(ROOT/'desktop/insightrover_desktop/__init__.py'))['VERSION']
    bundle = OUT/'dist/InsightRover'
    installer = OUT/f'installer/InsightRover-Setup-{version}-x64.exe'
    ico = (ROOT/'desktop/assets/insightrover.ico').read_bytes()
    _, kind, count = struct.unpack_from('<HHH', ico)
    assert kind == 1 and count >= 6
    expected = set()
    for i in range(count):
        entry = struct.unpack_from('<BBBBHHII', ico, 6+i*16)
        size, offset = entry[-2:]
        expected.add(hashlib.sha256(ico[offset:offset+size]).hexdigest())
    icons = {}
    for path in (bundle/'InsightRover.exe', installer):
        pe = pefile.PE(str(path))
        data = []
        for resource in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if resource.id == 3:
                for item in resource.directory.entries:
                    for language in item.directory.entries:
                        info = language.data.struct
                        data.append(pe.get_data(info.OffsetToData, info.Size))
        assert expected <= {hashlib.sha256(value).hexdigest() for value in data}, path
        icons[path.name] = {'branded_sizes': count, 'verified': True}
        pe.close()
    forbidden = {'smart_patrol.db','smart_patrol.db-wal','key.txt','motion-mapping.json','config.json','小车IP.txt'}
    assert not [str(path) for path in bundle.rglob('*') if path.name in forbidden]
    helper = bundle/'_internal/vehicle/vehicle_start.sh'
    assert helper.read_text(encoding='utf-8') == (ROOT/'desktop/insightrover_desktop/vehicle_start.sh').read_text(encoding='utf-8')
    assert 'ros2-smart-car-avoidance-20261028-66c66971/start_test.sh' in helper.read_text()
    embedded = bundle/'_internal/web'
    for path in (ROOT/'web-console/dist').rglob('*'):
        if path.is_file():
            assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256((embedded/path.relative_to(ROOT/'web-console/dist')).read_bytes()).digest()
    assert any('主动避障' in path.read_text(encoding='utf-8') for path in (embedded/'assets').glob('*.js'))
    acceptance = json.loads((OUT/'acceptance-result.json').read_text(encoding='utf-8'))
    assert len(acceptance['rounds']) == 2 and acceptance['upgrade_backup']
    release = ROOT/'releases'/f'v{version}'
    release.mkdir(parents=True, exist_ok=True)
    destination = release/installer.name
    shutil.copy2(installer, destination)
    shutil.copy2(ROOT/'desktop/README.md', release/'使用说明.md')
    result = {'version': version, 'installer': destination.name, 'bytes': destination.stat().st_size,
              'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
              'frozen_acceptance': acceptance, 'icons': icons, 'no_user_data_or_credentials': True,
              'embedded_frontend_verified': True, 'avoidance_proxy_verified': True,
              'vehicle_start_entry_verified': True, 'vehicle_driving_acceptance': False}
    (release/'交付验证.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (release/'SHA256SUMS.txt').write_text(result['sha256']+'  '+destination.name+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
