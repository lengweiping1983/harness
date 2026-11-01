import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {test} from 'node:test';

const helper = new URL('../../harness/llm/opencode_local.mjs', import.meta.url).href;
function run(fetchSource, timeout = 500) {
  return spawnSync(process.execPath, ['--input-type=module', '-e', `
    import assert from 'node:assert/strict';
    import {Readable} from 'node:stream';
    globalThis.Bun = {stdin:{stream:()=>Readable.toWeb(process.stdin)}};
    globalThis.fetch = async (url, options) => {
      assert.equal(url, 'https://opencode.ai/inference/openai/v1/chat/completions');
      assert.equal(options.timeout, false);
      assert.equal(options.redirect, 'error');
      const body = JSON.parse(options.body);
      assert.equal(body.max_completion_tokens, 64);
      assert.equal(body.reasoning_effort, 'high');
      assert.equal(body.max_tokens, undefined);
      assert.equal(options.headers['x-opencode-session'], 'fixture-session');
      ${fetchSource}
    };
    await import(${JSON.stringify(helper)});
  `], {input:JSON.stringify({body:{max_tokens:64,reasoning:{effort:'high'}},
    session:'fixture-session',headers:{},timeout_ms:timeout}), encoding:'utf8',timeout:5000});
}

test('local helper preserves response bytes and request mapping', () => {
  const result = run(`return new Response('data: [DONE]\\n\\n', {status:200});`);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout, '{"status":200,"retry_after":null}\ndata: [DONE]\n\n');
});

test('local helper reports a configured idle timeout without remote text', () => {
  const result = run(`return await new Promise((resolve,reject)=>{
    options.signal.addEventListener('abort',()=>reject(options.signal.reason), {once:true});
  });`, 40);
  assert.equal(result.status, 1);
  assert.equal(result.stdout, '');
  assert.deepEqual(JSON.parse(result.stderr), {failure:'idle_timeout',phase:'connect',network_error:null});
});

test('local helper reports known socket failures and hides arbitrary error messages', () => {
  const result = run(`throw Object.assign(new Error('secret request body'), {code:'ECONNRESET'});`);
  assert.equal(result.status, 1);
  assert.equal(result.stdout, '');
  assert.deepEqual(JSON.parse(result.stderr), {failure:'network',phase:'connect',network_error:'ConnectionResetError'});
  assert.ok(!result.stderr.includes('secret'));
});

test('local helper preserves partial bytes and classifies a broken response stream', () => {
  const result = run(`return new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: partial\\n\\n'));
      setTimeout(()=>controller.error(Object.assign(new Error('private'),{code:'ConnectionClosed'})),10);
    }
  }));`);
  assert.equal(result.status, 1);
  assert.ok(result.stdout.endsWith('data: partial\n\n'));
  assert.deepEqual(JSON.parse(result.stderr), {failure:'network',phase:'read',network_error:'ConnectionResetError'});
});

test('local helper resets idle time on headers and each body chunk', () => {
  const result = run(`await new Promise(resolve=>setTimeout(resolve,60));
    return new Response(new ReadableStream({start(controller) {
      let sent=0;
      const timer=setInterval(()=>{
        controller.enqueue(new TextEncoder().encode('x'));
        if(++sent===4){clearInterval(timer);controller.close();}
      },60);
      options.signal.addEventListener('abort',()=>{clearInterval(timer);controller.error(options.signal.reason);});
    }}));`, 180);
  assert.equal(result.status, 0, result.stderr);
  assert.ok(result.stdout.endsWith('xxxx'));
});

test('local helper leaves unknown runtime failures non-network', () => {
  const result = run(`throw new TypeError('private configuration');`);
  assert.equal(result.status, 1);
  assert.deepEqual(JSON.parse(result.stderr), {failure:'runtime',phase:'connect',network_error:null});
});
