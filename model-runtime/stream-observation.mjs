// 每个桥接进程只处理一个操作；只报告真实收到的字节或供应商事件，不定时伪造活动。
export function streamActivity(send, timeoutMs, clock = () => performance.now()) {
  const interval = Math.min(1000, timeoutMs / 4);
  let last = -Infinity;
  let bytes = 0;
  return (size = 0) => {
    bytes += size;
    const now = clock();
    if (now - last < interval) return;
    last = now;
    send({type: 'activity', network_bytes: bytes});
  };
}

export function observeFetch(fetch, activity) {
  return async (...args) => {
    const response = await fetch(...args);
    activity();
    if (!response.body) return response;
    const reader = response.body.getReader();
    let cancelled = false;
    const body = new ReadableStream({
      async pull(controller) {
        try {
          const {value, done} = await reader.read();
          if (cancelled) return;
          if (done) {reader.releaseLock(); controller.close(); return;}
          activity(value.byteLength);
          controller.enqueue(value);
        } catch (error) {
          reader.releaseLock();
          throw error;
        }
      },
      async cancel(reason) {
        cancelled = true;
        try {await reader.cancel(reason);} finally {reader.releaseLock();}
      },
    }, {highWaterMark: 0});
    const observed = new Response(body, {status: response.status, statusText: response.statusText, headers: response.headers});
    for (const key of ['url', 'redirected', 'type']) Object.defineProperty(observed, key, {value: response[key]});
    return observed;
  };
}

export function errorIdentity(error) {
  const constructor = error?.constructor?.name ?? '';
  const sdkType = /^(APIConnection(?:Timeout)?Error)\d*$/.exec(constructor)?.[1];
  let code;
  for (let cause = error, depth = 0; cause && depth < 8; cause = cause.cause, depth++) {
    if (typeof cause.code === 'string') code = cause.code;
  }
  // 固定供应商库明确报告缺少终止帧；这与任意 JSON/协议解析错误分别处理。
  if (new Set(['Stream ended without finish_reason', 'OpenAI Responses stream ended before a terminal response event',
    'OpenAI Responses stream ended without a stop reason', 'Anthropic stream ended before message_stop',
    'Anthropic stream ended without a stop reason']).has(error?.message)) code = 'HARNESS_INCOMPLETE_STREAM';
  return {name: sdkType ?? error?.name, code};
}

export function failure(error, status = 0) {
  const source = error.harnessError ?? errorIdentity(error);
  const code = source.code;
  return {type: 'failure', status: Number(error.harnessStatus || error.status || status),
    message: String(error.errorMessage ?? error.message ?? error),
    ...(typeof source.name === 'string' ? {error_type: source.name} : {}),
    ...(typeof code === 'string' ? {error_code: code} : {})};
}
