// 由已安装的 OpenCode Bun 运行时执行；不执行模型返回的工具。

let idleTimer;
let idleExpired = false;
let phase = 'connect';
try {
  const input = JSON.parse(await new Response(Bun.stdin.stream()).text());
  const body = input.body;
  if ('max_tokens' in body) {
    body.max_completion_tokens = body.max_tokens;
    delete body.max_tokens;
  }
  if (body.reasoning && Object.keys(body.reasoning).length === 1 && body.reasoning.effort) {
    body.reasoning_effort = body.reasoning.effort;
    delete body.reasoning;
  }
  const session = input.session ?? 'ses_' + ((~BigInt(Date.now() * 4096 + 1)) & ((1n << 48n)-1n)).toString(16).padStart(12,'0')
    + crypto.randomUUID().replaceAll('-','').slice(0,14);
  const headers = {...input.headers, Authorization:'Bearer public', 'Content-Type':'application/json'};
  for (const key of ['x-opencode-session','x-opencode-session-id','x-session-affinity','X-Session-Id']) headers[key] = session;
  const controller=new AbortController();
  const resetIdle=()=>{
    clearTimeout(idleTimer);
    idleTimer=setTimeout(()=>{ idleExpired=true; controller.abort(); },input.timeout_ms);
  };
  resetIdle();
  const response = await fetch('https://opencode.ai/inference/openai/v1/chat/completions', {
    method:'POST',headers,body:JSON.stringify(body),signal:controller.signal,redirect:'error',timeout:false,
  });
  phase = 'read';
  resetIdle();
  process.stdout.write(JSON.stringify({status:response.status,retry_after:response.headers.get('retry-after')})+'\n');
  if(response.body) {
    for await(const chunk of response.body) {
      resetIdle();
      if(!process.stdout.write(chunk)) await new Promise(resolve=>process.stdout.once('drain',resolve));
    }
  }
} catch(error) {
  // 只发送固定诊断字段，避免运行时异常正文泄露请求或凭据。
  const errors = {ConnectionClosed:'ConnectionResetError', ECONNRESET:'ConnectionResetError',
    ConnectionRefused:'ConnectionRefusedError', ECONNREFUSED:'ConnectionRefusedError',
    EPIPE:'BrokenPipeError', ETIMEDOUT:'TimeoutError', TimeoutError:'TimeoutError',
    ENOTFOUND:'gaierror', EAI_AGAIN:'gaierror'};
  const code = error?.code ?? error?.name;
  const networkError = Object.hasOwn(errors, code) ? errors[code] : null;
  process.stderr.write(JSON.stringify({failure:idleExpired ? 'idle_timeout' : networkError ? 'network' : 'runtime',
    phase, network_error:networkError})+'\n');
  process.exitCode=1;
} finally {
  clearTimeout(idleTimer);
}
