import {canonicalChat} from './page.mjs';
import {api} from './client.mjs';

const ALARM = 'harness-companion';
let running = false;
let edits = Promise.resolve();
function editBinding(expectedId, update) {
  const operation = edits.then(async () => {
    const {binding} = await chrome.storage.local.get('binding');
    if (expectedId && binding?.id !== expectedId) return;
    const value = update(binding);
    if (value) await chrome.storage.local.set({binding: value});
  });
  edits = operation.catch(() => {});
  return operation;
}
void chrome.storage.local.setAccessLevel({accessLevel: 'TRUSTED_CONTEXTS'});

async function saveStatus(reason, enabled, expectedId) {
  if (!expectedId) return;
  const resetIdle = ['waiting_executor', 'waiting_reply', 'offline', 'reply_unrecognized', 'busy', 'draft', 'composing', 'user_active'].includes(reason);
  await editBinding(expectedId, binding => binding && ({...binding, reason, checkedAt: Date.now(),
    ...(resetIdle ? {idleKey: '', idleSince: 0} : {}), ...(enabled === undefined ? {} : {enabled})}));
  await chrome.action.setBadgeText({text: reason === 'working' ? '…' : reason === 'complete' ? '✓' : reason ? '!' : 'ON'});
}

export async function tick() {
  if (running) return;
  running = true;
  let activeId;
  const status = (reason, enabled) => saveStatus(reason, enabled, activeId);
  try {
    const {settings, binding} = await chrome.storage.local.get(['settings', 'binding']);
    activeId = binding?.id;
    if (!settings || !binding?.enabled) return;
    if (Date.now() > binding.expiresAt) return await status('time_limit', false);
    try {
      const tab = await chrome.tabs.get(binding.tabId);
      if (canonicalChat(tab.url) !== binding.url) return await status('wrong_chat', false);
    } catch {return await status('wrong_chat', false);}
    const state = await api(settings, '/state?binding_id=' + encodeURIComponent(binding.id));
    if (state.url !== binding.url) return await status('wrong_chat', false);
    let page;
    try {page = await chrome.tabs.sendMessage(binding.tabId, {type: 'probe', url: binding.url});}
    catch {return await status('page_unavailable', false);}
    if (state.pending) {
      if (page.userMessages?.some(text => text.includes(state.pending.prompt))) {
        await api(settings, '/receipt', {binding_id: binding.id, id: state.pending.id, outcome: 'confirmed'});
        return await status('waiting_reply');
      }
      const {pending} = await chrome.storage.local.get('pending');
      if (pending?.id === state.pending.id && pending.outcome === 'not_sent') {
        await api(settings, '/receipt', {binding_id: binding.id, id: pending.id, outcome: 'not_sent'});
        await chrome.storage.local.remove('pending');
        return;
      }
      return await status('send_unconfirmed', false);
    }
    if (state.state === 'complete') return await status('complete', false);
    if (['blocked', 'paused'].includes(state.state)) return await status(state.state, false);
    if (page.reason === 'unsupported_page') return await status('unsupported_page', false);
    if (state.state === 'offline') return await status('offline');
    if (state.state === 'working') return await status('waiting_executor');
    if (!page.ready) return await status(page.reason || 'waiting_reply');
    if (!page.hasAssistant) return await status('reply_unrecognized');
    let phase = state.state === 'image' ? 'generate' : 'continue';
    if (phase === 'generate' && state.sent_phases.includes('generate')) {
      if (page.hasImage && page.assistantKey !== binding.imageBaseline) phase = 'deliver';
      else return await status('waiting_progress');
    }
    if (state.sent_phases.includes(phase)) return await status('waiting_progress');
    // 页面连续两次空闲才领取发送意图，避免把短暂控件切换当作生成结束。
    if (binding.idleKey !== page.assistantKey || Date.now() - (binding.idleSince || 0) < 3000) {
      await editBinding(binding.id, current => ({...current, idleKey: page.assistantKey,
        idleSince: current.idleKey === page.assistantKey ? current.idleSince : Date.now()}));
      return await status('stabilizing');
    }
    const dispatch = await api(settings, '/claim', {binding_id: binding.id, milestone: state.milestone, phase});
    const {binding: current} = await chrome.storage.local.get('binding');
    if (current?.id !== binding.id || !current.enabled) {
      await api(settings, '/receipt', {binding_id: binding.id, id: dispatch.id, outcome: 'not_sent'});
      return;
    }
    await chrome.storage.local.set({pending: dispatch});
    if (phase === 'generate') await editBinding(binding.id, value => ({...value, imageBaseline: page.assistantKey}));
    let result;
    try {result = await chrome.tabs.sendMessage(binding.tabId, {type: 'dispatch', url: binding.url, prompt: dispatch.prompt});}
    catch {result = {outcome: 'unknown', reason: 'send_unconfirmed'};}
    await chrome.storage.local.set({pending: {...dispatch, outcome: result.outcome || 'unknown'}});
    await api(settings, '/receipt', {binding_id: binding.id, id: dispatch.id, outcome: result.outcome || 'unknown'});
    await chrome.storage.local.remove('pending');
    if (result.outcome === 'unknown') await status(result.reason || 'send_unconfirmed', false);
    else await status(result.outcome === 'confirmed' ? 'waiting_reply' : result.reason || 'busy');
  } catch (error) {
    const stops = ['pairing_required', 'pairing_denied', 'bridge_response_invalid', 'bridge_request_failed'];
    await status(stops.includes(error.message) ? error.message : 'bridge_unavailable', stops.includes(error.message) ? false : undefined);
  }
  finally {running = false;}
}

async function ensureAlarm() {
  if (!await chrome.alarms.get(ALARM)) await chrome.alarms.create(ALARM, {periodInMinutes: 0.5});
}
void ensureAlarm();
chrome.runtime.onStartup.addListener(ensureAlarm);
chrome.runtime.onInstalled.addListener(ensureAlarm);
chrome.alarms.onAlarm.addListener(alarm => {if (alarm.name === ALARM) void tick();});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html')) return;
  if (message.type === 'start') {
    editBinding(null, () => message.binding).then(() => {void tick(); respond({ok: true});}, () => respond({ok: false}));
    return true;
  }
  if (message.type === 'stop') {
    editBinding(null, binding => binding && ({...binding, enabled: false, reason: 'paused'}))
      .then(async () => {
        const {binding} = await chrome.storage.local.get('binding');
        if (binding) try {await chrome.tabs.sendMessage(binding.tabId, {type: 'stop'});} catch {}
        respond({ok: true});
      }, () => respond({ok: false}));
    return true;
  }
  if (message.type === 'tick') {tick().then(() => respond({ok: true})); return true;}
});
