// 页面适配器只使用已核实的控件身份；界面不匹配时停止，不猜测其他按钮。
function observationKey(value) {
  // 仅用于观察变化，不把图片临时地址或回复正文写入扩展持久状态。
  let hash = 14695981039346656037n;
  for (let index = 0; index < value.length; index++) hash = BigInt.asUintN(64, (hash ^ BigInt(value.charCodeAt(index))) * 1099511628211n);
  return hash.toString(16).padStart(16, '0');
}

export function canonicalChat(value) {
  const url = new URL(value);
  if (url.origin !== 'https://chatgpt.com' || !/^\/(?:g\/[^/]+\/)?c\/[0-9a-f-]{36}$/.test(url.pathname)) throw new Error('wrong_chat');
  return url.origin + url.pathname;
}

function visible(node) {
  if (!node || node.hidden) return false;
  const view = node.ownerDocument?.defaultView;
  const style = view?.getComputedStyle(node);
  if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
  const rects = node.getClientRects?.();
  return !rects || rects.length > 0;
}

function editors(doc) {
  const explicit = [...doc.querySelectorAll('#prompt-textarea, #mobile-composer-prompt, textarea[name="prompt-textarea"]')].filter(visible);
  if (explicit.length) return explicit;
  const roots = [...doc.querySelectorAll('form[data-chatgpt-composer], form, [data-composer-wrap-inner], [data-composer-wrap-outer], [data-composer-primary]')].filter(visible);
  const scoped = [...new Set(roots.flatMap(root => [...root.querySelectorAll('textarea, [contenteditable="true"][role="textbox"]')]).filter(visible))];
  if (scoped.length) return scoped;
  return [...doc.querySelectorAll('textarea, [contenteditable="true"][role="textbox"]')].filter(visible);
}

function editor(doc) {
  const candidates = editors(doc);
  return candidates.length === 1 ? candidates[0] : null;
}

function composer(editorNode) {
  const scoped = editorNode?.closest('form[data-chatgpt-composer]') || editorNode?.closest('form[data-mobile-composer]') || editorNode?.closest('form')
    || editorNode?.closest('[data-composer-wrap-inner], [data-composer-wrap-outer], [data-composer-primary]')
    || editorNode?.closest('[data-composer-editor-host]');
  if (scoped) return scoped;
  const doc = editorNode?.ownerDocument;
  const known = [...(doc?.querySelectorAll('button[data-composer-submit][type="submit"], button#composer-submit-button, button[aria-label="Send message"], button[aria-label="Send prompt"], button[aria-label="发送消息"], button[data-testid="send-button"]') || [])].filter(visible);
  return known.length === 1 ? doc.body : null;
}

function textOf(node) {
  return 'value' in node ? node.value : node.textContent;
}

function hasAttachments(form) {
  return !!form?.querySelector('img, [data-file-id], [data-testid*="attachment"]')
    || [...(form?.querySelectorAll('input[type="file"]') || [])].some(input => input.files.length);
}

function latestTurnState(doc) {
  return [...doc.querySelectorAll('[data-turn-key] [data-talvt-turn-state]')].at(-1);
}

function responseError(doc, form) {
  const turn = latestTurnState(doc)?.closest('[data-turn-key]');
  const main = turn?.closest('main');
  if (!turn || !main || !form || !main.contains(form)) return null;
  return [...main.querySelectorAll('aside[role="alert"]')].find(alert => {
    if (!visible(alert) || form.contains(alert)) return false;
    if (!turn.contains(alert) && !(turn.compareDocumentPosition(alert) & 4)) return false;
    if (!(alert.compareDocumentPosition(form) & 4)) return false;
    const danger = [...alert.classList].some(name => name.startsWith('bg-background-danger-surface') || name === 'border-border-danger-surface');
    const buttons = [...alert.querySelectorAll('button')].filter(visible);
    return danger && buttons.length === 1 && ['Retry', '重试'].includes(buttons[0].textContent.trim());
  }) || null;
}

function busy(doc, form, allowInterrupted = false) {
  const legacyStop = [...doc.querySelectorAll('[data-testid="stop-button"]')].some(visible);
  const modernStop = [...(form?.querySelectorAll('[data-composer-stop-icon], [data-composer-loading-icon]') || [])].some(visible);
  const turnState = latestTurnState(doc);
  const state = turnState?.getAttribute('data-talvt-turn-state');
  const failure = responseError(doc, form);
  const retry = [...(failure?.querySelectorAll('button') || [])].find(visible);
  const retryPending = !!retry && (retry.disabled || retry.getAttribute('aria-disabled') === 'true');
  const unfinishedTurn = !!turnState && state !== 'complete' && !(allowInterrupted && (state === 'cancelled' || failure));
  const dialog = [...doc.querySelectorAll('[role="dialog"]')].some(visible);
  return legacyStop || modernStop || unfinishedTurn || retryPending || dialog;
}

function sendButton(form) {
  if (!form) return null;
  const modern = [...form.querySelectorAll('button[data-composer-submit][type="submit"], button#composer-submit-button, button[aria-label="Send message"], button[aria-label="Send prompt"], button[aria-label="发送消息"]')].filter(visible);
  if (modern.length === 1) return modern[0];
  const legacy = [...form.querySelectorAll('button[data-testid="send-button"]')].filter(visible);
  if (legacy.length === 1) return legacy[0];
  const submits = [...form.querySelectorAll('button[type="submit"]')].filter(visible);
  return submits.length === 1 ? submits[0] : null;
}

function messageNodes(doc, role) {
  const legacy = [...doc.querySelectorAll(`[data-message-author-role="${role}"]`)];
  const headed = [...doc.querySelectorAll(`h4[data-conversation-role="${role}"]`)];
  const modern = headed.map(heading => {
    const turn = heading.closest('[data-turn-key]');
    if (!turn) return null;
    const content = heading.nextElementSibling;
    if (role === 'user' && content?.matches('[data-chatgpt-search-message-ids]') && content.classList.contains('group/user-message')) return content;
    // 标题只声明角色，实际正文和纯图片位于同角色的最大消息区域中。
    let region = heading;
    while (region.parentElement && turn.contains(region.parentElement)) {
      const parent = region.parentElement;
      if ([...parent.querySelectorAll('h4[data-conversation-role]')].some(node => node.getAttribute('data-conversation-role') !== role)) break;
      region = parent;
    }
    return region;
  }).filter(Boolean);
  return [...new Set([...legacy, ...modern])].filter(node => ![...legacy, ...modern].some(other => other !== node && other.contains(node)))
    .sort((left, right) => left.compareDocumentPosition(right) & 4 ? -1 : 1);
}

function userMessages(doc) {
  return messageNodes(doc, 'user');
}

function messageBody(node) {
  const body = node.cloneNode(true);
  for (const heading of body.querySelectorAll('h4[data-conversation-role], [data-markdown-copy="exclude"]')) heading.remove();
  return body.textContent.trim();
}

export function inspectPage(doc, expectedUrl, currentUrl, composing = false, recentlyActive = false, allowInterrupted = false) {
  if (canonicalChat(currentUrl) !== expectedUrl) return {ready: false, reason: 'wrong_chat'};
  const messages = userMessages(doc);
  const assistants = messageNodes(doc, 'assistant');
  const currentEditor = editor(doc);
  const form = composer(currentEditor);
  const turnState = latestTurnState(doc);
  const interrupted = turnState?.getAttribute('data-talvt-turn-state') === 'cancelled' || !!responseError(doc, form);
  const interruptedRoot = allowInterrupted && interrupted ? turnState.closest('[data-turn-key]') : null;
  const interruptedTurn = interruptedRoot?.getAttribute('data-turn-key') || '';
  const candidate = assistants.at(-1);
  const last = interruptedRoot && (!candidate || !interruptedRoot.contains(candidate)) ? undefined : candidate;
  const images = [...(last?.querySelectorAll('img') || [])].filter(node => node.complete && node.naturalWidth > 0);
  const identity = interruptedTurn || last?.getAttribute('data-message-id') || last?.closest('[data-message-id]')?.getAttribute('data-message-id')
    || last?.closest('[data-turn-key]')?.getAttribute('data-turn-key') || '';
  const assistantKey = last || interruptedTurn ? observationKey(JSON.stringify([identity, last?.textContent.slice(-200) || '',
    images.map(node => node.getAttribute('data-file-id') || node.currentSrc || node.getAttribute('src'))])) : '';
  const draft = currentEditor ? textOf(currentEditor).trim() : '';
  const attachments = hasAttachments(form);
  const isBusy = busy(doc, form, allowInterrupted);
  const ready = !!currentEditor && !!form && !attachments && !draft && !composing && !recentlyActive && !isBusy;
  return {ready, reason: !currentEditor || !form ? 'unsupported_page' : attachments ? 'draft' : draft ? 'draft' : composing ? 'composing' : recentlyActive ? 'user_active' : isBusy ? 'busy' : ready ? '' : 'busy',
    userMessages: messages.map(node => node.textContent),
    assistantKey, hasImage: images.length > 0, hasAssistant: !!last, interrupted};
}

export async function sendOnce(doc, expectedUrl, getUrl, prompt, guard, insert = fill => fill(), allowInterrupted = false) {
  if (!guard()) return {outcome: 'not_sent', reason: 'page_changed'};
  const currentEditor = editor(doc);
  const form = composer(currentEditor);
  if (!currentEditor || !form) return {outcome: 'not_sent', reason: 'unsupported_page'};
  if (canonicalChat(getUrl()) !== expectedUrl) return {outcome: 'not_sent', reason: 'wrong_chat'};
  if (hasAttachments(form) || busy(doc, form, allowInterrupted)) return {outcome: 'not_sent', reason: 'busy'};
  const before = userMessages(doc);
  const previousMatches = before.filter(node => messageBody(node) === prompt).length;
  const inserted = insert(() => {
    currentEditor.focus();
    if ('value' in currentEditor) {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(currentEditor), 'value')?.set;
      setter ? setter.call(currentEditor, prompt) : currentEditor.value = prompt;
    } else if (!doc.execCommand('insertText', false, prompt)) return false;
    currentEditor.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: prompt}));
    return true;
  });
  if (!inserted) return {outcome: 'not_sent', reason: 'unsupported_page'};
  const text = () => textOf(currentEditor).trim();
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (canonicalChat(getUrl()) !== expectedUrl || editor(doc) !== currentEditor || composer(currentEditor) !== form || text() !== prompt) return {outcome: 'unknown', reason: 'page_changed'};
    if (hasAttachments(form) || busy(doc, form, allowInterrupted)) return {outcome: 'unknown', reason: 'user_active'};
    const button = sendButton(form);
    if (button && !button.disabled) {
      if (!guard(true) || hasAttachments(form) || busy(doc, form, allowInterrupted)) return {outcome: 'unknown', reason: 'user_active'};
      button.click();
      const confirmUntil = Date.now() + 10000;
      while (Date.now() < confirmUntil) {
        if (canonicalChat(getUrl()) !== expectedUrl) return {outcome: 'unknown', reason: 'wrong_chat'};
        const messages = userMessages(doc);
        if (messages.length > before.length && messages.filter(node => messageBody(node) === prompt).length > previousMatches) return {outcome: 'confirmed'};
        await new Promise(resolve => setTimeout(resolve, 200));
      }
      return {outcome: 'unknown', reason: 'send_unconfirmed'};
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  return {outcome: 'unknown', reason: 'unsupported_page'};
}
