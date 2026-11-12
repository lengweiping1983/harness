import semver from 'semver';
import {createHash, randomBytes, randomUUID, timingSafeEqual} from 'node:crypto';
import {createServer} from 'node:http';
import {platform, arch, release} from 'node:os';

export const deepSeekModels = [
  {id: 'deepseek-flash', name: 'DeepSeek-V4.1-Flash', api: 'anthropic-messages', baseUrl: 'https://api.deepseek.com/anthropic', input: ['text', 'image'], contextWindow: 1048576, maxTokens: 128000, reasoning: true, thinkingLevelMap: {off: undefined, minimal: null, low: 'low', medium: null, high: 'high', xhigh: null, max: 'max'}},
  {id: 'deepseek-v4-pro', name: 'DeepSeek-V4-Pro', api: 'anthropic-messages', baseUrl: 'https://api.deepseek.com/anthropic', input: ['text'], contextWindow: 1048576, maxTokens: 128000, reasoning: true, thinkingLevelMap: {off: undefined, minimal: null, low: 'low', medium: null, high: 'high', xhigh: null, max: 'max'}},
];
export const deepSeekProviders = ['deepseek-official', 'deepseek-account'].map(id => ({id, name: id === 'deepseek-account' ? 'DeepSeek Account' : 'DeepSeek Official', base_url: 'https://api.deepseek.com/anthropic', dynamic: true, api_key: id !== 'deepseek-account', oauth: id === 'deepseek-account', env: id === 'deepseek-account' ? [] : ['DEEPSEEK_API_KEY'], models: deepSeekModels}));

async function boundedJson(response, limit = 1024 * 1024) {
  if (!response.ok || !response.body) {const error = Error(`HTTP ${response.status}`); error.status = response.status; throw error;}
  const reader = response.body.getReader(); let size = 0; const buffers = [];
  try {while (true) {const next = await reader.read(); if (next.done) break; size += next.value.length; if (size > limit) throw Error('Response exceeds size limit'); buffers.push(next.value);} return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(Buffer.concat(buffers)));} finally {await reader.cancel(); reader.releaseLock();}
}

export async function deepSeekLogin(request, send, receive, signal) {
  const origin = 'https://platform.deepseek.com';
  const verifier = randomBytes(32).toString('base64url'), state = randomBytes(32).toString('base64url');
  const headers = {'content-type': 'application/json', 'x-client-bundle-id': '', 'x-client-platform': 'web', 'x-client-version': request.client_version, 'x-client-locale': request.language === 'zh-CN' ? 'zh_CN' : 'en_US', 'x-client-timezone-offset': '0'};
  const post = async (method, body) => {
    const payload = await boundedJson(await fetch(`${origin}/auth-api/v0/dsh/${method}`, {method: 'POST', headers, body: JSON.stringify(body), redirect: 'error', signal: AbortSignal.timeout(30000)}), 65536);
    if (payload.code !== 0 || payload.data?.biz_code !== 0) throw Error('DeepSeek authorization rejected');
    return payload.data.biz_data;
  };
  let callback, authorizeId, authorized = false;
  const code = Promise.withResolvers(); code.promise.catch(() => {});
  const abort = () => code.reject(Error('Authorization cancelled'));
  signal?.addEventListener('abort', abort, {once: true});
  if (signal?.aborted) abort();
  const server = createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1'); const received = url.searchParams.get('state') ?? '';
    const valid = Buffer.byteLength(received) === Buffer.byteLength(state) && timingSafeEqual(Buffer.from(received), Buffer.from(state));
    if (req.method !== 'GET' || url.pathname !== '/oauth/callback' || !valid || !url.searchParams.get('code') || url.searchParams.getAll('state').length !== 1 || url.searchParams.getAll('code').length !== 1) {res.writeHead(400).end(); return;}
    if (callback) {res.writeHead(410).end(); return;}
    callback = res; code.resolve(url.searchParams.get('code'));
  });
  await new Promise((resolve, reject) => server.once('error', reject).listen(0, '127.0.0.1', resolve));
  const redirect = `http://127.0.0.1:${server.address().port}/oauth/callback`;
  const timer = setTimeout(() => code.reject(Error('Authorization expired')), 600000);
  try {
    const init = await post('auth_init', {code_challenge: createHash('sha256').update(verifier).digest('base64url'), code_challenge_method: 'S256', state, redirect_uri: redirect, locale: request.language === 'zh-CN' ? 'zh_CN' : 'en_US', login_source: 'web'});
    const url = new URL(init.authorize_url); if (url.origin !== origin || url.pathname !== '/dsh/authorize') throw Error('Invalid authorization destination');
    authorizeId = init.authorize_id;
    send({type: 'auth', event: {type: 'auth_url', url: url.href}});
    const grant = await post('auth_exchange', {code: await code.promise, code_verifier: verifier, redirect_uri: redirect, device_id: request.device_id || randomUUID(), device_model: `${platform()}-${arch()}`, os_version: release()});
    const completion = new URL(grant.authorized_url); if (completion.origin !== origin || completion.pathname !== '/dsh/authorized' || typeof grant.token !== 'string' || !grant.token) throw Error('Invalid authorization response');
    send({type: 'credential', credential: {type: 'oauth', access: grant.token, refresh: '', expires: Number.MAX_SAFE_INTEGER, issuer: origin, inference_origin: 'https://api.deepseek.com'}});
    if ((await receive()).type !== 'committed') throw Error('Credential was not committed');
    authorized = true;
    callback?.writeHead(302, {location: completion.href, 'cache-control': 'no-store'}).end();
    send({type: 'result', value: {authorized: true}});
  } finally {
    signal?.removeEventListener('abort', abort);
    clearTimeout(timer); callback?.end(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    if (!authorized && authorizeId) await post('auth_cancel', {authorize_id: authorizeId, code_verifier: verifier});
  }
}

export async function deepSeekListing(baseUrl, headers, supportedVersion) {
  const payload = await boundedJson(await fetch(new URL('/models', baseUrl), {headers, redirect: 'error', signal: AbortSignal.timeout(15000)}));
  if (!Array.isArray(payload.data)) throw Error('Invalid DeepSeek model listing');
  const compare = version => semver.valid(version) !== null && semver.gte(supportedVersion, version);
  return payload.data.map(row => {
    if (typeof row.id !== 'string' || !row.id) throw Error('Invalid DeepSeek model identity');
    const object = value => typeof value === 'object' && value !== null && !Array.isArray(value);
    if (row.client_requirements !== undefined && !object(row.client_requirements)) return {...row, supported: false};
    const requirements = row.client_requirements?.['deepseek-harness'];
    if (requirements !== undefined && !object(requirements)) return {...row, supported: false};
    const minimum = requirements?.min_version;
    const supported = minimum !== undefined ? typeof minimum === 'string' && compare(minimum) : deepSeekModels.some(model => model.id === row.id);
    return {...row, supported, minimum, name: row.name || row.id, contextWindow: row.context_window, maxTokens: row.max_output_tokens, input: row.input_modalities ?? ['text']};
  });
}
