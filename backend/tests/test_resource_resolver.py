from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.api.routes.resources import ResourceReference
from app.models import MapModel, MapPlace, Route
from app.services.resource_resolver import resolve_references


def fixture():
    items = {(MapModel, "map"): SimpleNamespace(id="map", active=True, name="实验室", image_key="maps/map/rev.png", width=100, height=100),
             (MapPlace, "a"): SimpleNamespace(id="a", map_id="map", name="A", x=25, y=30, yaw=0),
             (Route, "route"): SimpleNamespace(map_id="map", status="active", parameters='{"place_ids":["a"]}')}
    return SimpleNamespace(get=lambda model, ident: items.get((model, ident)))


def reference(source="business_place", ident="a"):
    return ResourceReference(source=source, resource_id=ident, map_id="map")


def test_model_coordinates_are_forbidden():
    with pytest.raises(ValidationError):
        ResourceReference(source="business_place", resource_id="a", map_id="map", x=99)


def test_vehicle_reference_cannot_mix_with_business_coordinates():
    ref = reference("vehicle_place", "desk")
    wire = resolve_references(fixture(), [ref], {"maps.read"})
    assert wire == {"vehicle_place_ids": ["desk"], "drawing_id": "map"}
    with pytest.raises(ValueError):
        resolve_references(fixture(), [ref, reference()], {"maps.read"})


def test_resource_lookup_requires_authentication(client):
    assert client.get("/api/v1/resources/places?robot_id=robot").status_code == 401
    assert client.get("/api/v1/resources/routes").status_code == 401


def test_authenticated_lookup_preserves_literal_search_and_offline_flag(client, login):
    headers = {"Authorization": f"Bearer {login('operator', 'operator123')}"}
    response = client.get("/api/v1/resources/places", params={"robot_id": "unbound", "keyword": "%"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "vehicle_places_available": False}
    response = client.get("/api/v1/resources/routes", params={"keyword": "%"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


def test_route_expands_only_persisted_places():
    value = resolve_references(fixture(), [reference("business_route", "route")], {"maps.read", "routes.read"})
    assert value["points"][0]["drawing"] == {"x": 25, "y": 30}
    assert value["drawing"]["revision"] == "maps/map/rev.png"


def test_route_permission_is_independent():
    with pytest.raises(PermissionError):
        resolve_references(fixture(), [reference("business_route", "route")], {"maps.read"})


def test_missing_place_and_cross_map_are_rejected():
    with pytest.raises(ValueError):
        resolve_references(fixture(), [reference(ident="missing")], {"maps.read"})
    other = ResourceReference(source="business_place", resource_id="a", map_id="other")
    with pytest.raises(ValueError):
        resolve_references(fixture(), [reference(), other], {"maps.read"})


@pytest.mark.parametrize("expected,status", [(None, 200), ("b" * 64, 409)])
def test_resolve_endpoint_checks_confirmation_version(monkeypatch, expected, status):
    from fastapi import HTTPException
    from app.api.routes import resources
    calls = []

    async def request(path, wire):
        calls.append(path)
        assert wire["points"][0]["drawing"] == {"x": 25, "y": 30}
        return {"map_key": "active-map", "resource_version": "a" * 64, "goals": []}

    monkeypatch.setattr(resources, "vehicle_binding", lambda db: ("robot", "http://fixture"))
    monkeypatch.setattr(resources, "robot_context_service", SimpleNamespace(context=lambda *args: {
        "available": True, "telemetry": {"active_map_key": "active-map"}}))
    monkeypatch.setattr(resources, "get_robot_gateway", lambda url: SimpleNamespace(navigation_request=request))
    payload = resources.ResolveIn(robot_id="robot", references=[reference()], expected_version=expected)
    user = SimpleNamespace(permissions={"maps.read", "robots.read"})
    coroutine = resources.resolve(payload, fixture(), user)
    if status == 409:
        with pytest.raises(HTTPException) as error:
            coroutine.send(None)
        assert error.value.status_code == 409
    else:
        with pytest.raises(StopIteration) as done:
            coroutine.send(None)
        assert done.value.value["resource_version"] == "a" * 64
    assert calls == ["resolve-resources"]
