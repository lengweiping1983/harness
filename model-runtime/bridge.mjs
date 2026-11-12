import {deepSeekLogin, deepSeekProviders, deepSeekModels, deepSeekListing} from './deepseek.mjs';
import {createInterface} from 'node:readline';
import {access} from 'node:fs/promises';
import {homedir} from 'node:os';
import {builtinModels} from '@earendil-works/pi-ai/providers/all';
import {createProvider} from '@earendil-works/pi-ai/models';
import {openAICompletionsApi} from '@earendil-works/pi-ai/api/openai-completions.lazy';
import {openAIResponsesApi} from '@earendil-works/pi-ai/api/openai-responses.lazy';
import {anthropicMessagesApi} from '@earendil-works/pi-ai/api/anthropic-messages.lazy';
import {failure, observeFetch, streamActivity} from './stream-observation.mjs';

const cancellation = new AbortController();
const credentialTasks = new Set();
const lines = createInterface({input: process.stdin});
const queue = []; const waiting = [];
lines.on('line', line => { try {if (JSON.parse(line).type === 'cancel') {cancellation.abort(); return;}} catch {} const receive = waiting.shift(); if (receive) receive(line); else queue.push(line); });
lines.on('close', () => { for (const receive of waiting.splice(0)) receive(null); });
const receive = async () => { const line = queue.length ? queue.shift() : await new Promise(resolve => waiting.push(resolve)); if (line === null) throw Error('Input closed'); return JSON.parse(line); };
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
const protocols = {'openai-completions': openAICompletionsApi, 'openai-responses': openAIResponsesApi, 'anthropic-messages': anthropicMessagesApi};
const zero = {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0};

export function tokenUsage(usage) {
  const input = usage.input + usage.cacheRead + usage.cacheWrite;
  return {input_tokens: input, output_tokens: usage.output, reasoning_tokens: usage.reasoning ?? 0,
    cache_read_tokens: usage.cacheRead, cache_reported_input_tokens: usage.cacheRead > 0 || usage.cacheWrite > 0 ? input : 0};
}

export function transcript(messages, model) {
  const result = []; const system = [];
  const block = item => {
    if (item.type === 'image' || item.type === 'image_url') {
      const match = /^data:([^;,]+);base64,(.*)$/s.exec(item.url ?? item.image_url?.url);
      if (!match) throw Error('Images must be supplied as inline data');
      return {type: 'image', mimeType: match[1], data: match[2]};
    }
    if (item.type === 'tool_use') return {type: 'toolCall', id: item.id, name: item.name, arguments: item.input};
    if (item.type === 'thinking') return {type: 'thinking', thinking: item.thinking, ...(item.signature ? {thinkingSignature: item.signature} : {})};
    return {type: 'text', text: item.text ?? ''};
  };
  const toolNames = new Map();
  for (const message of messages) {
    if (message.role === 'system' || message.role === 'developer') { system.push(message.content); continue; }
    if (message.role === 'tool') {
      result.push({role: 'toolResult', toolCallId: message.tool_call_id, toolName: toolNames.get(message.tool_call_id) ?? '', content: [{type: 'text', text: message.content}], isError: !!message.is_error, timestamp: 0});
    } else if (message._kind === 'tool_result') {
      for (const value of message.content) result.push({role: 'toolResult', toolCallId: value.tool_use_id, toolName: toolNames.get(value.tool_use_id) ?? '', content: typeof value.content === 'string' ? [{type: 'text', text: value.content}] : value.content.map(block), isError: value.is_error, timestamp: 0});
    } else if (message.role === 'assistant') {
      const sameModel = message.model === model.harnessIdentity;
      const values = typeof message.content === 'string' ? (message.content ? [{type: 'text', text: message.content}] : []) : message.content;
      for (const call of message.tool_calls ?? []) values.push({type: 'tool_use', id: call.id, name: call.function.name, input: JSON.parse(call.function.arguments)});
      if (message.reasoning_content) values.unshift({type: 'thinking', thinking: message.reasoning_content});
      for (const detail of message.reasoning_details ?? []) if (detail.type === 'reasoning.text') values.unshift({type: 'thinking', thinking: detail.text || '', signature: sameModel ? detail.signature : undefined});
      for (const value of values) if (value.type === 'tool_use') toolNames.set(value.id, value.name);
      const content = values.map(block);
      for (const replay of message.reasoning_details ?? []) if (replay.type === 'harness_provider_replay' && replay.api === model.api && replay.model === model.id && replay.scope === model.harnessIdentity) {for (const value of replay.blocks) {const target = content.find(item => item.type === value.type && (item.id ? item.id === value.id : item.type === 'thinking' ? item.thinking === value.thinking : item.text === value.text)); if (target) Object.assign(target, value); else if (value.type === 'thinking') content.unshift(value);}}
      result.push({role: 'assistant', content, api: model.api, provider: model.provider, model: message.model === model.harnessIdentity ? model.id : message.model || model.id, usage: {...zero, cost: {...zero}}, stopReason: message.stop_reason === 'tool_use' || message.tool_calls?.length ? 'toolUse' : 'stop', timestamp: 0});
    } else result.push({role: 'user', content: typeof message.content === 'string' ? message.content : message.content.map(block), timestamp: 0});
  }
  return {systemPrompt: system.join('\n\n'), messages: result};
}

async function main() {
  const request = await receive();
  if (request.version !== 1) throw Error('Unsupported bridge protocol');
  let credential = request.credential ?? undefined;
  const credentials = {
    read: async () => credential,
    list: async () => credential ? [{providerId: request.provider, type: credential.type}] : [],
    modify: async (_id, mutate) => {const work = (async () => {send({type: 'credential_pending'}); const next = await mutate(credential); if (next !== undefined) {send({type: 'credential', credential: next}); const ack = await receive(); if (ack.type !== 'committed') throw Error('Credential was not committed'); credential = next;} else send({type: 'credential_unchanged'}); return credential;})(); credentialTasks.add(work); try {return await work;} finally {credentialTasks.delete(work);}},
    delete: async () => { throw Error('Logout is owned by Harness'); },
  };
  const environment = request.connection?.auth?.env ?? {};
  const models = builtinModels({credentials, authContext: {
    env: async name => environment[name] || process.env[name] || undefined,
    fileExists: async path => {try {await access(path.startsWith('~') ? homedir() + path.slice(1) : path); return true;} catch {return false;}},
  }});
  if (request.operation === 'catalog') {
    send({type: 'result', value: models.getProviders().map(provider => ({id: provider.id, name: provider.name, base_url: provider.baseUrl ?? '', api_key: !!provider.auth.apiKey, oauth: !!provider.auth.oauth, models: provider.getModels()}))}); return;
  }
  let provider = models.getProvider(request.provider);
  if (request.operation === 'login' && request.provider === 'deepseek-account') {await deepSeekLogin(request, send, receive, cancellation.signal); return;}
  if (request.operation === 'login') {
    if (!provider) throw Error('Unknown provider');
    await models.login(provider.id, request.auth_type ?? 'oauth', {
      signal: cancellation.signal,
      notify: event => send({type: 'auth', event}),
      prompt: async prompt => { const {signal, ...view} = prompt; send({type: 'prompt', prompt: view}); const response = await receive(); if (response.type !== 'answer') throw Error('Authorization cancelled'); return response.value; },
    }, {agentName: 'Harness', getDeviceId: () => request.device_id});
    send({type: 'result', value: {authorized: true}}); return;
  }
  if (request.operation === 'discover' && deepSeekProviders.some(entry => entry.id === request.provider)) {
    const headers = request.provider === 'deepseek-account' ? {'x-dsh-auth-token': credential?.access ?? ''} : {Authorization: `Bearer ${credential?.key || credential?.env?.DEEPSEEK_API_KEY || process.env.DEEPSEEK_API_KEY || ''}`};
    const value = await deepSeekListing(request.connection.base_url, headers, '0.2.2-alpha.2');
    send({type: 'result', value: value.filter(entry => entry.supported)}); return;
  }
  if (request.operation === 'discover') {
    if (!provider) throw Error('Unknown provider');
    const refreshed = await models.refresh({providers: [provider.id], force: true});
    if (refreshed.errors.size) throw [...refreshed.errors.values()][0];
    send({type: 'result', value: provider.getModels()}); return;
  }
  const connection = request.connection;
  if (request.operation === 'check') { send({type: 'result', value: await models.checkAuth(request.provider) ?? null}); return; }
  if (connection.model_catalog === undefined && provider?.refreshModels) {const refreshed = await models.refresh({providers: [provider.id]}); if (refreshed.errors.size) throw [...refreshed.errors.values()][0];}
  const raw = request.model;
  const deepSeek = deepSeekProviders.some(entry => entry.id === request.provider);
  const cached = connection.model_catalog?.find(model => model.id === raw.model);
  const original = connection.model_catalog !== undefined ? (cached ? {...models.getModel(request.provider, raw.model), ...cached} : undefined) : deepSeek ? deepSeekModels.find(model => model.id === raw.model) : models.getModel(request.provider, raw.model);
  const knownApis = [...new Set((connection.model_catalog ?? provider?.getModels() ?? []).map(model => model.api).filter(Boolean))];
  // OpenRouter 为目录外聊天模型提供统一的 Chat Completions 入口；目录混用协议不代表新模型必须手选。
  const endpointApi = request.provider === 'openrouter' ? 'openai-completions' : undefined;
  const api = (deepSeek ? 'anthropic-messages' : connection.protocol) || original?.api || endpointApi || (knownApis.length === 1 ? knownApis[0] : undefined);
  if (!api) throw Error('The model needs an explicit API protocol');
  // 自定义接口的推理能力不代表支持 developer；默认保留 system，避免开启推理改变指令角色。
  // Qwen 的日期快照和手工模型同样沿用端点的角色限制。
  // 仅提供协议默认值；目录、接口和模型的显式声明依次覆盖。
  const roleCompat = api === 'openai-completions' && ['custom', 'qwen-token-plan', 'qwen-token-plan-cn', 'qwen-token-plan-individual'].includes(request.provider)
    ? {supportsDeveloperRole: false} : {};
  const model = {...original, harnessIdentity: request.config?.model, id: raw.model, name: raw.name || raw.model, provider: request.provider, api,
    baseUrl: (!connection.protocol && connection.base_url === provider?.baseUrl ? original?.baseUrl : connection.base_url) || original?.baseUrl || provider?.baseUrl,
    cost: original?.cost ?? {...zero}, input: raw.input ?? original?.input ?? ['text'],
    contextWindow: raw.context_window ?? original?.contextWindow ?? 128000,
    maxTokens: raw.max_output_tokens ?? original?.maxTokens ?? 8192,
    reasoning: raw.reasoning_efforts === false ? false : raw.reasoning_efforts ? true : original?.reasoning ?? !!(original?.thinkingLevelMap || original?.reasoningEfforts?.length),
    compat: {...roleCompat, ...original?.compat, ...connection.compat, ...raw.compat},
  };
  // 目录只报告档位列表时也建立完整映射，避免运行库把已声明的 max/xhigh 降为 high。
  const declaredEfforts = raw.reasoning_efforts ?? (original?.reasoningEfforts
    ? Object.fromEntries(original.reasoningEfforts.map(level => [level, original.thinkingLevelMap?.[level] ?? (level === 'off' ? null : level)])) : undefined);
  if (declaredEfforts && declaredEfforts !== false) model.thinkingLevelMap = {...Object.fromEntries(['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'].map(level => [level, null])), ...Object.fromEntries(Object.entries(declaredEfforts).map(([level, value]) => [level, level === 'off' && value === null ? undefined : value]))};
  if (deepSeek) {
    let auth;
    if (request.provider === 'deepseek-account') {
      if (!credential?.access || new URL(model.baseUrl).origin !== credential.inference_origin) throw Error('DeepSeek account authorization is required for this origin');
      auth = {headers: {'x-dsh-auth-token': credential.access}, apiKey: 'account'};
    } else {const key = credential?.key || credential?.env?.DEEPSEEK_API_KEY || process.env.DEEPSEEK_API_KEY; if (!key) throw Error('DeepSeek API key is required'); auth = {apiKey: key};}
    const headers = {...auth.headers, ...(request.provider === 'deepseek-official' ? {Authorization: `Bearer ${auth.apiKey}`} : {})};
    const listing = await deepSeekListing(model.baseUrl, headers, '0.2.2-alpha.2');
    const listed = listing.find(entry => entry.id === model.id); if (listed && !listed.supported) throw Error(`CLIENT_UPGRADE_REQUIRED: ${listed.minimum ?? model.id}`);
    provider = createProvider({id: request.provider, models: [model], auth: {apiKey: {name: 'DeepSeek', resolve: async () => ({auth})}}, api: anthropicMessagesApi()});
  } else if (connection.protocol) {
    const factory = protocols[connection.protocol]; if (!factory) throw Error('Unsupported custom protocol');
    provider = createProvider({id: request.provider, name: request.provider, baseUrl: connection.base_url, models: [model], auth: {apiKey: {name: 'Harness', resolve: async () => ({auth: credential?.key ? {apiKey: credential.key} : {}})}}, api: factory()});
  } else {
    if (!provider) throw Error('Unknown provider');
    provider = {...provider, ...(connection.base_url ? {baseUrl: connection.base_url} : {}), getModels: () => [model]};
  }
  models.setProvider(provider);
  const context = {...transcript(request.messages, model), tools: request.tools ?? []};
  const config = request.config ?? {};
  // 真实输出能力与运行库必需的占位容量分开，未知能力不能制造 8192 的生成限制。
  const outputCapacity = raw.max_output_tokens ?? original?.maxTokens;
  const budgets = [config.max_tokens, config.extra_body?.max_tokens, config.extra_body?.max_completion_tokens,
    config.extra_body?.max_output_tokens].filter(value => Number.isSafeInteger(value) && value > 0);
  const requestedTokens = budgets.length ? Math.min(...budgets) : undefined;
  const maxTokens = Math.min(requestedTokens ?? outputCapacity ?? model.maxTokens, outputCapacity ?? Infinity);
  let status = 0;
  let providerFailure;
  const activity = streamActivity(send, connection.timeout_ms ?? 300000);
  // Google 等原生适配器直接使用全局 fetch；作用域限于本次所属子进程。
  globalThis.fetch = observeFetch(globalThis.fetch, activity, value => {providerFailure = value;});
  const stream = models.streamSimple(model, context, {
    signal: cancellation.signal,
    fetch: globalThis.fetch,
    env: environment, temperature: config.temperature ?? undefined, maxTokens, reasoning: config.reasoning_effort ?? undefined,
    sessionId: request.session_id ?? undefined, headers: {...connection.headers, ...Object.fromEntries(Object.entries(config.extra_headers ?? {}).filter(([name]) => !['authorization', 'x-api-key', 'host', 'x-dsh-auth-token'].includes(name.toLowerCase())))},
    timeoutMs: connection.timeout_ms ?? 300000, maxRetries: connection.max_retries ?? 2,
    onPayload: payload => {
      const body = {...payload, ...config.extra_body, model: payload.model ?? model.id};
      if (api === 'openai-completions' || api === 'openai-responses') {
        // 模型默认不等于关闭思考；仅保留调用方显式提供的推理参数。
        if (config.reasoning_effort == null) {
          if (!Object.hasOwn(config.extra_body ?? {}, 'reasoning')) delete body.reasoning;
          if (!Object.hasOwn(config.extra_body ?? {}, 'reasoning_effort')) delete body.reasoning_effort;
        }
        // 可选上限默认交给提供商；显式预算沿用适配器的字段与上下文收敛结果。
        const field = api === 'openai-responses' ? 'max_output_tokens' : Object.hasOwn(payload, 'max_tokens') ? 'max_tokens' : 'max_completion_tokens';
        delete body.max_tokens; delete body.max_completion_tokens; delete body.max_output_tokens;
        if (requestedTokens !== undefined && payload[field] !== undefined) body[field] = Math.min(payload[field], maxTokens);
      }
      if (deepSeek) {
        const effort = config.reasoning_effort || 'high';
        if (!['off', 'low', 'high', 'max'].includes(effort)) throw Error('Unsupported DeepSeek reasoning level');
        body.thinking = {type: effort === 'off' ? 'disabled' : 'enabled'};
        delete body.output_config;
        if (effort !== 'off') body.output_config = {effort};
      }
      send({type: 'request', body, model: model.id, protocol: model.api, base_url: model.baseUrl});
      return body;
    },
    onResponse: response => { status = response.status; activity(); },
    onProviderStreamEvent: () => activity(),
  });
  for await (const event of stream) {
    // 非 fetch 的原生传输仍可按真实参数片段报告活动，完整调用前不交付工具。
    if (event.type === 'toolcall_delta') activity();
    if (event.type === 'start') send({type: 'event', event: {kind: 'start'}});
    if (event.type === 'text_delta') send({type: 'event', event: {kind: 'text', text: event.delta}});
    if (event.type === 'thinking_delta') send({type: 'event', event: {kind: 'thinking', thinking: event.delta}});
    if (event.type === 'thinking_end') { const value = event.partial.content[event.contentIndex]; if (value?.thinkingSignature) send({type: 'event', event: {kind: 'signature', signature: value.thinkingSignature}}); }
    if (event.type === 'toolcall_end') send({type: 'event', event: {kind: 'tool_use', tool_use: {id: event.toolCall.id, name: event.toolCall.name, input: event.toolCall.arguments, ...(event.toolCall.argumentsError ? {arguments_error: event.toolCall.argumentsError} : {})}}});
    if (event.type === 'error') { send(failure(event.error, status, providerFailure)); return; }
    if (event.type === 'done') {
      const replay = event.message.content.filter(block => block.thoughtSignature || block.thinkingSignature || block.textSignature);
      if (replay.length) send({type: 'event', event: {kind: 'reasoning_details', reasoning_details: [{type: 'harness_provider_replay', api: model.api, model: model.id, scope: model.harnessIdentity, blocks: replay}]}});
      const usage = event.message.usage;
      send({type: 'event', event: {kind: 'usage', usage: tokenUsage(usage)}});
      send({type: 'event', event: {kind: 'done', stop_reason: event.reason === 'toolUse' ? 'tool_use' : event.reason === 'length' ? 'max_tokens' : 'stop'}});
    }
  }
}

if (process.env.HARNESS_BRIDGE_TEST === '1') lines.close();
else {
  main().catch(error => { send(failure(error)); process.exitCode = 1; }).finally(async () => {await Promise.allSettled([...credentialTasks]); lines.close();});
}
