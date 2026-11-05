"""Catch invalid literal ROS names before deploying on a ROS-enabled machine."""
import ast
from pathlib import Path
import re


def test_literal_ros_topics_have_valid_tokens():
    package = Path(__file__).resolve().parents[1] / "ros2_ws/src/smart_car_decision/smart_car_decision"
    checked = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr not in {"create_publisher", "create_subscription", "create_client", "create_service"}:
                continue
            if len(call.args) < 2 or not isinstance(call.args[1], ast.Constant):
                continue
            name = call.args[1].value
            if not isinstance(name, str):
                continue
            checked.append(name)
            assert all(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", token)
                       for token in name.lstrip("/").split("/")), (path.name, call.lineno, name)
    assert "/robot/control_lease/request" in checked
    assert "/robot/control_lease/status" in checked
