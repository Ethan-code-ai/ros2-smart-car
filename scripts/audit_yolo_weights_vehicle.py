"""Read-only exact legacy-weight inventory and active model configuration."""
import stage_avoidance_release as remote

if __name__=='__main__':
    c=remote.connect('192.168.1.106')
    try:
        print(remote.python(c,'''from pathlib import Path
import json,yaml,ultralytics
items=[{'path':str(p),'bytes':p.stat().st_size} for p in Path('/home/jetson').rglob('yolo11*.pt') if p.is_file()]
config=Path('/home/jetson/ros2-smart-car-avoidance-20261028-66c66971/ros2_ws/src/smart_car_decision/config/decision.yaml')
v=yaml.safe_load(config.read_text())
print(json.dumps({'weights':items,'model':v['yolo11_camera_node']['ros__parameters'].get('model_path'),'library':ultralytics.__file__},indent=2))
''',timeout=40))
    finally:c.close()
