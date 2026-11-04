"""Explicit online preparation only; running vehicle service never downloads models."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args()
    directory=args.directory.expanduser().resolve()
    directory.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(directory).free<2*1024**3:
        raise RuntimeError('准备模型至少需要 2 GiB 空闲空间；不自动清理现有文件')
    os.chdir(directory)
    from ultralytics import YOLOE
    import numpy as np
    model=YOLOE('yoloe-11s-seg.pt')
    model.set_classes(['server rack','oscilloscope','laboratory power supply','microscope','bottle','cardboard box','bag','backpack','chair'])
    model.predict(np.zeros((640,640,3),dtype=np.uint8),device='cpu',verbose=False)
    model.set_classes(['person','stop sign','traffic light','car','truck','bus','motorcycle','bicycle'])
    model.save('yoloe-11s-traffic.pt')
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.suffix in {'.pt','.ts'}}
    result={'model':'yoloe-11s-seg.pt','sha256':files,'ultralytics':importlib.metadata.version('ultralytics'),
            'torch':importlib.metadata.version('torch'),'mobile_acceptance':False}
    (directory/'model-manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
