"""Verify the frozen application and installer and write software build evidence."""
import argparse
import hashlib
import json
import struct
from pathlib import Path
import runpy

import pefile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'output/windows-app-20261028-v0311'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--credential-env-file", type=Path)
    parser.add_argument("--package-only", action="store_true",
                        help="Check packaged resources without claiming a frozen functional acceptance")
    args = parser.parse_args()
    out = args.output_dir.resolve()
    version = runpy.run_path(str(ROOT/'desktop/insightrover_desktop/__init__.py'))['VERSION']
    bundle = out/'dist/InsightRover'
    installer = out/f'installer/InsightRover-Setup-{version}-x64.exe'
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
    assert not [str(path) for path in bundle.rglob('*') if path.name in forbidden or path.name.startswith('.env')]
    secrets = []
    if args.credential_env_file:
        for line in args.credential_env_file.read_text(encoding='utf-8-sig').splitlines():
            key, separator, value = line.partition('=')
            if separator and key.strip() in {'ASSISTANT_API_KEY', 'ROBOT_SERVICE_TOKEN'}:
                value = value.strip().strip('"').strip("'")
                if value:
                    if len(value) < 8:
                        raise ValueError("凭据长度不足，无法进行冻结包检查")
                    secrets.append(value.encode())
        for path in bundle.rglob('*'):
            if path.is_file():
                contents = path.read_bytes()
                if any(secret in contents for secret in secrets):
                    raise ValueError("冻结包内发现配置凭据，请检查打包资源")
    helper = bundle/'_internal/vehicle/vehicle_start.sh'
    assert helper.read_text(encoding='utf-8') == (ROOT/'desktop/insightrover_desktop/vehicle_start.sh').read_text(encoding='utf-8')
    embedded = bundle/'_internal/web'
    for path in (ROOT/'web-console/dist').rglob('*'):
        if path.is_file():
            assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256((embedded/path.relative_to(ROOT/'web-console/dist')).read_bytes()).digest()
    assert any('主动避障' in path.read_text(encoding='utf-8') for path in (embedded/'assets').glob('*.js'))
    acceptance = None
    if not args.package_only:
        acceptance = json.loads((out/'acceptance-result.json').read_text(encoding='utf-8'))
        assert len(acceptance['rounds']) == 2 and acceptance['upgrade_backup']
        assert acceptance['windows_credentials'] and all(item['exit_code'] == 0 for item in acceptance['rounds'])
    result = {'version': version, 'installer': installer.name, 'bytes': installer.stat().st_size,
              'package_only': args.package_only, 'functional_validation_repeated': False if args.package_only else True,
              'sha256': hashlib.sha256(installer.read_bytes()).hexdigest(),
              'frozen_acceptance': acceptance, 'icons': icons, 'no_user_data_or_credentials': True,
              'embedded_frontend_verified': True, 'avoidance_proxy_verified': True,
              'configured_credential_literals_checked': bool(secrets),
              'vehicle_start_entry_verified': True, 'vehicle_driving_acceptance': False}
    (out/'software-build-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (out/'SHA256SUMS.txt').write_text(result['sha256']+'  '+installer.name+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
