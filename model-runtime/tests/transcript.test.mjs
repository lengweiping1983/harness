import assert from 'node:assert/strict';
import {test} from 'node:test';
process.env.HARNESS_BRIDGE_TEST = '1';
const {transcript, tokenUsage} = await import('../bridge.mjs');
const model = {id: 'same-name', api: 'anthropic-messages', provider: 'anthropic', harnessIdentity: 'harness/supplier/endpoint/model'};
test('signed replay belongs to its configured model identity, including its connection', () => {
  const signed = {type: 'thinking', thinking: 'thought', thinkingSignature: 'opaque'};
  const message = {role: 'assistant', model: model.harnessIdentity, content: 'answer', reasoning_details: [{type: 'harness_provider_replay', api: model.api, model: model.id, scope: model.harnessIdentity, blocks: [signed]}]};
  assert.equal(transcript([message], model).messages[0].content[0].thinkingSignature, 'opaque');
  const foreign = transcript([message], {...model, harnessIdentity: 'harness/other/endpoint/model'}).messages[0];
  assert.equal(foreign.content.some(block => block.thinkingSignature), false);
  assert.equal(foreign.model, model.harnessIdentity);
});
test('tool results keep pairing and error status, and user images stay binary input', () => {
  const context = transcript([{role: 'user', content: [{type: 'image_url', image_url: {url: 'data:image/png;base64,Yg=='}}]}, {role: 'assistant', model: model.harnessIdentity, content: '', tool_calls: [{id: 'call', function: {name: 'lookup', arguments: '{"value":42}'}}]}, {role: 'tool', tool_call_id: 'call', content: 'denied', is_error: true}], model);
  assert.deepEqual(context.messages[0].content, [{type: 'image', mimeType: 'image/png', data: 'Yg=='}]);
  assert.equal(context.messages[1].content[0].arguments.value, 42);
  assert.equal(context.messages[2].toolName, 'lookup');
  assert.equal(context.messages[2].isError, true);
});

test('cache usage carries its input denominator without inventing reports for unknown zeros', () => {
  assert.deepEqual(tokenUsage({input: 7, output: 9, cacheRead: 5, cacheWrite: 3}), {
    input_tokens: 15, output_tokens: 9, reasoning_tokens: 0, cache_read_tokens: 5, cache_reported_input_tokens: 15,
  });
  assert.equal(tokenUsage({input: 7, output: 9, cacheRead: 0, cacheWrite: 3}).cache_reported_input_tokens, 10);
  assert.equal(tokenUsage({input: 7, output: 9, cacheRead: 0, cacheWrite: 0}).cache_reported_input_tokens, 0);
});
