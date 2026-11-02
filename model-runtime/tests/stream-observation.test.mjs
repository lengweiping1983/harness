import assert from 'node:assert/strict';
import {test} from 'node:test';
import {APIConnectionError, APIConnectionTimeoutError} from 'openai';
import {errorIdentity, failure, observeFetch, streamActivity} from '../stream-observation.mjs';

test('activity is driven by received data and retains cumulative bytes when throttled', () => {
  const reports = [];
  let time = 0;
  const activity = streamActivity(value => reports.push(value), 400, () => time);
  activity(5);
  time = 50;
  activity(7);
  time = 100;
  activity(3);
  time = 10000;
  assert.deepEqual(reports.map(value => value.network_bytes), [5, 15]);
  activity();
  assert.equal(reports.length, 3);
});

test('fetch observation preserves response metadata, binary bytes and cancellation ownership', async () => {
  const reports = [];
  let cancelled = 0;
  const source = new Response(new ReadableStream({
    start(controller) {controller.enqueue(Uint8Array.of(0, 255, 42));},
    cancel() {cancelled++;},
  }), {status: 200, headers: {'content-type': 'text/event-stream'}});
  Object.defineProperty(source, 'url', {value: 'https://example.invalid/stream'});
  const response = await observeFetch(async () => source, size => reports.push(size ?? 0))();
  assert.equal(response.url, source.url);
  assert.equal(response.headers.get('content-type'), source.headers.get('content-type'));
  const reader = response.body.getReader();
  assert.deepEqual((await reader.read()).value, Uint8Array.of(0, 255, 42));
  await reader.cancel();
  assert.equal(cancelled, 1);
  assert.deepEqual(reports, [0, 3]);
});

test('SDK transport causes survive formatting while malformed protocol failures stay distinct', () => {
  assert.deepEqual(errorIdentity(new APIConnectionTimeoutError()), {name: 'APIConnectionTimeoutError', code: undefined});
  const error = new APIConnectionError({cause: new TypeError('fetch failed', {cause: Object.assign(new Error('socket'), {code: 'ECONNRESET'})})});
  assert.deepEqual(failure(error), {type: 'failure', status: 0, message: 'Connection error.', error_type: 'APIConnectionError', error_code: 'ECONNRESET'});
  assert.equal(errorIdentity(new Error('Stream ended without finish_reason')).code, 'HARNESS_INCOMPLETE_STREAM');
  assert.equal(errorIdentity(new SyntaxError('Invalid JSON')).code, undefined);
});
