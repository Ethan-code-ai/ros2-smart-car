import test from 'node:test';
import assert from 'node:assert/strict';
import { createRobotApi, normalizeRobotStatus } from '../src/robotApi';
import { MODE_LABELS } from '../src/liveControl';
import { ROBOT_API_PROXY_PATTERN } from '../src/robotProxy';

test('old vehicle and reconnect snapshots never invent active avoidance', () => {
  assert.equal(normalizeRobotStatus({ mode: 'stop' }).avoidance, undefined);
  const snapshot = normalizeRobotStatus({ avoidance: { supported: true, confirmed: false, available: false,
    settings: { enabled: true, trigger_distance_m: .6 }, state: 'waiting', reason: 'pending' } });
  assert.equal(snapshot.avoidance?.confirmed, false);
  assert.equal(MODE_LABELS.auto, '自动巡航');
  assert.match('/api/avoidance', new RegExp(ROBOT_API_PROXY_PATTERN));
  assert.doesNotMatch('/api/avoidance-extra', new RegExp(ROBOT_API_PROXY_PATTERN));
});

test('avoidance request preserves pending response instead of optimistic confirmation', async () => {
  const requests: [string, RequestInit | undefined][] = [];
  const api = createRobotApi((async (url: string, init?: RequestInit) => {
    requests.push([url, init]);
    return new Response(JSON.stringify({ supported: true, confirmed: false, state: 'waiting',
      settings: { enabled: false, trigger_distance_m: .6 } }), { status: 202 });
  }) as typeof fetch);
  const response = await api.setAvoidance({ enabled: false });
  assert.equal(response.confirmed, false);
  assert.equal(requests[0][0], '/api/avoidance');
  assert.deepEqual(JSON.parse(requests[0][1]?.body as string), { enabled: false });
});
