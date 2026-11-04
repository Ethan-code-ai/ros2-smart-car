"""Package vehicle source and explicit utilities; exclude weights, caches and user data."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import runpy

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'ros2_ws/src/smart_car_decision'
VERSION=runpy.run_path(str(ROOT/'desktop/insightrover_desktop/__init__.py'))['VERSION']
RELEASE=ROOT/'releases'/f'v{VERSION}'


def main():
    RELEASE.mkdir(parents=True,exist_ok=True)
    allowed={'.py','.yaml','.yml','.xml','.cfg','.txt','.md','.json','.html','.js','.css','.svg','.png','.ico'}
    files=[p for p in PACKAGE.rglob('*') if p.is_file() and (p.suffix in allowed or p.parent.name=='resource')
           and not any(part in {'__pycache__','models','node_modules','.pytest_cache'} for part in p.relative_to(PACKAGE).parts)]
    files += [ROOT/p for p in ['scripts/prepare_yoloe11.py','scripts/prepare_scan_provenance_patch.py',
        'scripts/benchmark_vision_service.py',f'docs/vision-search-{VERSION}.md',
        'docs/holonomic-avoidance-fix-20261028.md','docs/assumed-clear-policy-20261028.md']]
    files += [p for p in (ROOT/'web-console/dist').rglob('*') if p.is_file()]
    manifest={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    target=RELEASE/f'InsightRover-Vehicle-{VERSION}-source.tar.gz'
    with tarfile.open(target,'w:gz') as archive:
        for path in sorted(files):archive.add(path,arcname=str(path.relative_to(ROOT)).replace('\\','/'))
        content=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
        info=tarfile.TarInfo('source-sha256.json');info.size=len(content)
        archive.addfile(info,io.BytesIO(content))
    (RELEASE/'车端源码清单.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (RELEASE/'实施与待验收说明.md').write_bytes((ROOT/f'docs/vision-search-{VERSION}.md').read_bytes())
    (RELEASE/'横移避障修正说明.md').write_bytes((ROOT/'docs/holonomic-avoidance-fix-20261028.md').read_bytes())
    (RELEASE/'缺测处理说明.md').write_bytes((ROOT/'docs/assumed-clear-policy-20261028.md').read_bytes())
    artifacts=[p for p in RELEASE.iterdir() if p.suffix in {'.exe','.gz'}]
    (RELEASE/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in artifacts),encoding='utf-8')
    print(json.dumps({'archive':str(target),'files':len(files),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__=='__main__':main()
