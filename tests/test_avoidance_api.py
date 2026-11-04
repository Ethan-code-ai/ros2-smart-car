from smart_car_decision.avoidance_settings import AvoidanceService, DEFAULTS


def attach(node, path):
    node._ros_status_age_sec = .1
    node.store.update(mode='stop', odom={'ok': True, 'linear_speed_xy': 0., 'angular_speed': 0.})
    node.avoidance_service = AvoidanceService(node, path, lambda _: None)
    return node.avoidance_service


def test_old_vehicle_does_not_pretend_support(web_client):
    assert web_client.get('/api/avoidance').status_code == 501
    assert 'avoidance' not in web_client.get('/api/status').json()


def test_api_returns_pending_then_exact_controller_ack(fake_node, web_client, tmp_path):
    service = attach(fake_node, tmp_path/'settings.json')
    response = web_client.post('/api/avoidance', json={'enabled': False, 'trigger_distance_m': .8})
    assert response.status_code == 202
    assert not response.json()['confirmed']
    service.observe({'confirmed': True, 'available': True, 'settings': DEFAULTS, 'request_id': 'old', 'state': 'idle'})
    assert not web_client.get('/api/avoidance').json()['confirmed']
    service.observe({'confirmed': True, 'available': True, 'settings': service.requested,
                     'request_id': service.request_id, 'state': 'idle', 'robot_radius_m': .25})
    assert web_client.get('/api/status').json()['avoidance']['confirmed']
    with web_client.websocket_connect('/ws/status') as ws:
        assert ws.receive_json()['avoidance']['settings']['enabled'] is False


def test_moving_or_invalid_settings_rejected(fake_node, web_client, tmp_path):
    attach(fake_node, tmp_path/'settings.json')
    assert web_client.post('/api/avoidance', json={'enabled': 'yes'}).status_code == 422
    assert web_client.post('/api/avoidance', json={'trigger_distance_m': 0}).status_code == 422
    assert web_client.post('/api/avoidance', json={'unknown': 1}).status_code == 422
    fake_node.store.update(mode='manual')
    assert web_client.post('/api/avoidance', json={'enabled': False}).status_code == 409
    fake_node.store.update(mode='stop', odom={'ok': True, 'linear_speed_xy': .03, 'angular_speed': 0.})
    assert web_client.post('/api/avoidance', json={'enabled': False}).status_code == 409


def test_stop_request_stops_autonomous_mode(fake_node, web_client):
    fake_node.store.set_mode('auto')
    assert web_client.post('/api/command', json={'command': 'stop'}).status_code == 200
    assert fake_node.store.snapshot()['mode'] == 'stop'
