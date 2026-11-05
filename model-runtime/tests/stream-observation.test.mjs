import assert from 'node:assert/strict';
import {test} from 'node:test';
import {APIConnectionError, APIConnectionTimeoutError, APIError} from 'openai';
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

test('provider trace identity survives the SDK and formatted bridge failure', () => {
  const error = APIError.generate(409, {error: {code: 'MODEL_CALL_FAILED', message: 'upstream failed', traceId: 'trace-fixture'}}, undefined, new Headers());
  const identity = errorIdentity(error);
  assert.equal(identity.code, 'MODEL_CALL_FAILED');
  assert.equal(identity.traceId, 'trace-fixture');
  assert.equal(failure(error).provider_trace_id, 'trace-fixture');
  const formatted = failure({errorMessage: error.message, harnessError: identity, harnessStatus: 409});
  assert.equal(formatted.status, 409);
  assert.equal(formatted.error_code, 'MODEL_CALL_FAILED');
  assert.equal(formatted.provider_trace_id, 'trace-fixture');
  assert.equal(failure(Object.assign(new Error('malformed trace'), {error: {traceId: 42}})).provider_trace_id, undefined);
  assert.equal(errorIdentity(Object.assign(new Error('oversized trace'), {error: {traceId: 'x'.repeat(65537)}})).traceId, undefined);
});

test('provider error observation preserves raw bytes and bounded traces while controlling only model-call conflict retries', async () => {
  for (const [status, code, retry] of [[409, 'MODEL_CALL_FAILED', 'false'], [409, 'OTHER_CONFLICT', null],
    [429, 'MODEL_CALL_FAILED', null], [503, 'MODEL_CALL_FAILED', null]]) {
    const raw = JSON.stringify({error: {code, message: 'upstream failed'}, traceId: 'trace-response'});
    let diagnostic;
    const response = await observeFetch(async () => new Response(raw, {status}), () => {}, value => {diagnostic = value;})();
    assert.equal(await response.text(), raw);
    assert.deepEqual(diagnostic, {status, code, traceId: 'trace-response'});
    assert.equal(response.headers.get('x-should-retry'), retry);
    assert.equal(failure({errorMessage: 'failed', harnessStatus: status, harnessError: {name: 'Error'}}, 0, diagnostic).provider_trace_id, 'trace-response');
  }
  for (const raw of ['{', JSON.stringify({traceId: 'x'.repeat(65536)})]) {
    let diagnostic = 'stale';
    const response = await observeFetch(async () => new Response(raw, {status: 409}), () => {}, value => {diagnostic = value;})();
    assert.equal(await response.text(), raw);
    assert.equal(diagnostic, undefined);
    assert.equal(response.headers.get('x-should-retry'), null);
  }
});
