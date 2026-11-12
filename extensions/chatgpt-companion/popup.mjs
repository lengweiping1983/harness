import {canonicalChat} from './page.mjs';
import {api} from './client.mjs';
import {mountSecretInput} from './secret-input.mjs';
const surface = new URL(location.href);
const expanded = surface.searchParams.get('view') === 'tab';
const sourceTab = surface.searchParams.get('sourceTab');
const sourceChat = surface.searchParams.get('sourceChat');
const sourceTabId = /^[1-9]\d*$/.test(sourceTab || '') && Number.isSafeInteger(Number(sourceTab)) ? Number(sourceTab) : undefined;
document.documentElement.dataset.view = expanded ? 'tab' : 'popup';
let messages = {};
let busy = false;
let connected = false;
let stopping = false;
let opening = false;
let localeGeneration = 0;
let operationGeneration = 0;
let settingsEdits = Promise.resolve();
let checkedAt = 0;
let sessions = [];
const reasons = {status: 'not_enabled', 'connection-status': 'not_connected'};
const element = id => document.getElementById(id);
element('open-tab').hidden = expanded;
const renderTokenVisibility = mountSecretInput(element('token'), element('token-toggle'), key => messages[key]?.message);
function renderTarget() {
  const target = sessions.find(session => session.project_id === element('session').value);
  element('target-session').textContent = target ? messages.target_session.message.replace('{title}', target.title).replace('{project}', target.project_id) : '';
  element('target-session').title = element('target-session').textContent;
}
function renderCheckedAt() {
  const time = checkedAt ? new Date(checkedAt).toLocaleTimeString(document.documentElement.lang) : '';
  element('checked-at').textContent = time ? messages.last_checked.message.replace('{time}', time) : '';
}
function saveSettings(update) {
  const operation = settingsEdits.then(async () => {
    const {settings = {}} = await chrome.storage.local.get('settings');
    await chrome.storage.local.set({settings: update(settings)});
  });
  settingsEdits = operation.catch(() => {});
  return operation;
}
const show = (key, target = 'status') => {
  reasons[target] = key;
  element(target).textContent = messages[key]?.message || messages.request_failed?.message || key;
  if (target === 'connection-status') element(target).closest('.status-card').dataset.state = key;
};
function updateControls() {
  element('connect').disabled = busy;
  element('start').disabled = busy || !connected || !element('session').value;
  element('stop').disabled = stopping;
  for (const id of ['endpoint', 'token', 'session', 'continuous']) element(id).disabled = busy;
  element('token-toggle').disabled = busy;
}
async function localize(language) {
  const generation = ++localeGeneration;
  const loaded = await (await fetch('_locales/' + language + '/messages.json')).json();
  if (generation !== localeGeneration) return;
  messages = loaded;
  document.documentElement.lang = language === 'zh_CN' ? 'zh-CN' : 'en';
  for (const node of document.querySelectorAll('[data-key]')) node.textContent = messages[node.dataset.key].message;
  for (const node of document.querySelectorAll('[data-aria-key]')) node.setAttribute('aria-label', messages[node.dataset.ariaKey].message);
  renderTokenVisibility();
  for (const [target, key] of Object.entries(reasons)) show(key, target);
  renderCheckedAt();
  renderTarget();
}
async function control(message) {
  let timeout;
  try {
    const result = await Promise.race([chrome.runtime.sendMessage(message),
      new Promise((_, reject) => {timeout = setTimeout(() => reject(new Error('request_failed')), 5000);})]);
    if (!result?.ok) throw new Error('request_failed');
  } finally {clearTimeout(timeout);}
}
async function run(action, target = 'status') {
  if (busy) return;
  busy = true;
  const generation = ++operationGeneration;
  const current = () => generation === operationGeneration;
  updateControls();
  try {await action(current);}
  catch (error) {if (current()) show(messages[error.message] ? error.message : 'request_failed', target);}
  finally {busy = false; updateControls();}
}
function settingsFromFields() {
  let endpoint;
  try {endpoint = new URL(element('endpoint').value);} catch {throw new Error('invalid_endpoint');}
  if (endpoint.protocol !== 'http:' || endpoint.hostname !== '127.0.0.1' || endpoint.pathname !== '/' || endpoint.search || endpoint.hash || endpoint.username || endpoint.password) throw new Error('invalid_endpoint');
  const token = element('token').value.trim();
  if (!token) throw new Error('pairing_required');
  return {endpoint: endpoint.href, token, language: element('language').value, continueAfterTerminal: element('continuous').checked === true};
}
async function loadConnection(settings, current) {
  connected = false;
  show('checking_connection', 'connection-status');
  const health = await api(settings, '/health');
  if (!current()) return;
  if (health.protocol !== 'harness-chat-companion' || health.protocol_version !== 2
      || !['ready', 'offline', 'missing', 'invalid'].includes(health.executor)) throw new Error('bridge_response_invalid');
  if (health.executor === 'missing') throw new Error('executor_missing');
  if (health.executor === 'invalid') throw new Error('executor_invalid');
  const result = await api(settings, '/sessions');
  if (!current()) return;
  if (!Array.isArray(result.sessions) || result.sessions.some(session => !session || !/^he_[0-9a-f]{32}$/.test(session.session_id)
      || typeof session.title !== 'string' || typeof session.project_id !== 'string')) throw new Error('bridge_response_invalid');
  const {binding} = await chrome.storage.local.get('binding');
  if (!current()) return;
  sessions = result.sessions;
  const projects = [...new Set(sessions.map(session => session.project_id))];
  element('session').replaceChildren(...projects.map(project => {
    const option = document.createElement('option'); option.value = project;
    option.textContent = project; return option;
  }));
  const bound = sessions.find(session => session.session_id === binding?.sessionId);
  if (bound) element('session').value = bound.project_id;
  else if (projects.length > 1) element('session').selectedIndex = -1;
  renderTarget();
  connected = true;
  show(!result.sessions.length ? 'no_sessions' : health.executor === 'offline' ? 'executor_offline' : 'connected', 'connection-status');
}
const saved = await chrome.storage.local.get(['settings', 'binding']);
const draftId = surface.searchParams.get('draft');
const draftKey = expanded && /^[0-9a-f-]{36}$/.test(draftId || '') ? 'companion-draft:' + draftId : null;
const draft = draftKey ? (await chrome.storage.session.get(draftKey))[draftKey] : undefined;
if (draftKey) await chrome.storage.session.remove(draftKey);
checkedAt = saved.binding?.checkedAt || 0;
element('version').textContent = chrome.runtime.getManifest().version;
const language = draft?.language || saved.settings?.language || (navigator.language.startsWith('zh') ? 'zh_CN' : 'en');
element('language').value = language;
element('endpoint').value = draft?.endpoint ?? saved.settings?.endpoint ?? 'http://127.0.0.1:8877/';
element('token').value = draft?.token ?? saved.settings?.token ?? '';
element('continuous').checked = (draft?.continueAfterTerminal ?? saved.settings?.continueAfterTerminal) === true;
if (saved.binding?.reason) reasons.status = saved.binding.reason;
await localize(language);

element('language').addEventListener('change', event => {
  const selected = event.target.value;
  void localize(selected).catch(() => show('request_failed'));
  void saveSettings(settings => ({...settings, language: selected})).catch(() => show('request_failed'));
});
for (const id of ['endpoint', 'token']) element(id).addEventListener('input', () => {
  connected = false; sessions = []; element('session').replaceChildren(); renderTarget();
  show('not_connected', 'connection-status'); updateControls();
});
element('session').addEventListener('change', () => {updateControls(); renderTarget();});
element('continuous').addEventListener('change', () => {
  const selected = element('continuous').checked === true;
  return saveSettings(settings => ({...settings, continueAfterTerminal: selected})).catch(() => show('request_failed'));
});
element('open-tab').addEventListener('click', async () => {
  if (opening) return;
  opening = true;
  element('open-tab').disabled = true;
  let draftKey;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    const target = new URL(chrome.runtime.getURL('popup.html'));
    target.searchParams.set('view', 'tab');
    if (tab?.id) target.searchParams.set('sourceTab', String(tab.id));
    try {target.searchParams.set('sourceChat', canonicalChat(tab?.url));} catch {}
    const draftId = crypto.randomUUID();
    draftKey = 'companion-draft:' + draftId;
    await chrome.storage.session.set({[draftKey]: {endpoint: element('endpoint').value, token: element('token').value, language: element('language').value, continueAfterTerminal: element('continuous').checked === true}});
    target.searchParams.set('draft', draftId);
    await chrome.tabs.create({url: target.href});
  } catch {
    show('request_failed');
    if (draftKey) await chrome.storage.session.remove(draftKey);
  }
  finally {opening = false; element('open-tab').disabled = false;}
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.binding) {
    show(changes.binding.newValue?.reason || 'not_enabled');
    checkedAt = changes.binding.newValue?.checkedAt || 0;
    renderCheckedAt();
  }
});
element('connect').addEventListener('click', () => run(async current => {
  const settings = settingsFromFields();
  connected = false;
  await control({type: 'stop'});
  if (!current()) return;
  await saveSettings(() => ({...settings, language: element('language').value}));
  if (!current()) return;
  await loadConnection(settings, current);
}, 'connection-status'));
element('start').addEventListener('click', () => run(async current => {
  const settings = settingsFromFields();
  const continueAfterTerminal = element('continuous').checked === true;
  const sessionId = sessions.find(session => session.project_id === element('session').value)?.session_id;
  if (!connected || !sessionId) throw new Error('choose_session');
  let tab;
  try {
    if (expanded) {
      if (!sourceTabId || !sourceChat) throw new Error('open_chat');
      tab = await chrome.tabs.get(sourceTabId);
      if (canonicalChat(tab.url) !== sourceChat) throw new Error('open_chat');
    } else [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  } catch {throw new Error(expanded ? 'source_chat_unavailable' : 'open_chat');}
  if (!current()) return;
  let url;
  try {url = canonicalChat(tab?.url);} catch {throw new Error('open_chat');}
  let page;
  try {page = await chrome.tabs.sendMessage(tab.id, {type: 'probe', url});} catch {throw new Error('page_unavailable');}
  if (!current()) return;
  if (!page || page.reason === 'unsupported_page') throw new Error('unsupported_page');
  await control({type: 'stop'});
  if (!current()) return;
  const result = await api(settings, '/bind', {session_id: sessionId, url});
  if (!current()) return;
  if (!/^bind_[0-9a-f]{32}$/.test(result.binding_id) || result.url !== url) throw new Error('bridge_response_invalid');
  await control({type: 'start', binding: {id: result.binding_id, sessionId, tabId: tab.id, url, enabled: true, continueAfterTerminal, expiresAt: Date.now() + 8 * 3600000}});
  if (current()) show('enabled');
}));
element('stop').addEventListener('click', async () => {
  if (stopping) return;
  stopping = true; ++operationGeneration; updateControls();
  if (reasons['connection-status'] === 'checking_connection') {
    connected = false; show('not_connected', 'connection-status');
  }
  try {await control({type: 'stop'}); show('paused');}
  catch {show('request_failed');}
  finally {stopping = false; updateControls();}
});
updateControls();
if (saved.settings?.token && (!draft || (draft.endpoint === saved.settings.endpoint && draft.token === saved.settings.token))) void run(current => loadConnection(saved.settings, current), 'connection-status');
