(() => {
  let composing = false;
  let activeAt = 0;
  let sending = false;
  let generation = 0;
  let inserting = false;
  document.addEventListener('compositionstart', () => {composing = true;}, true);
  document.addEventListener('compositionend', () => {composing = false; activeAt = Date.now();}, true);
  for (const kind of ['keydown', 'pointerdown', 'input']) document.addEventListener(kind, event => {
    // execCommand 自身也产生可信 input；只豁免同步插入，真实操作仍受保护。
    if (event.isTrusted && !(kind === 'input' && inserting)) activeAt = Date.now();
  }, true);
  const adapter = import(chrome.runtime.getURL('page.mjs'));
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id !== chrome.runtime.id || !['probe', 'dispatch', 'stop'].includes(message.type)) return;
    if (message.type === 'stop') {generation++; respond({ok: true}); return;}
    (async () => {
      const {inspectPage, sendOnce} = await adapter;
      const probe = () => inspectPage(document, message.url, location.href, composing, Date.now() - activeAt < 3000, message.allowInterrupted === true);
      if (message.type === 'probe') return probe();
      if (sending) return {outcome: 'not_sent', reason: 'busy'};
      sending = true;
      const acceptedGeneration = generation;
      try {
        return await sendOnce(document, message.url, () => location.href, message.prompt, afterFill => {
          if (acceptedGeneration !== generation || composing || Date.now() - activeAt < 3000 || location.origin + location.pathname !== message.url) return false;
          const page = probe();
          if (message.assistantKey && page.assistantKey !== message.assistantKey) return false;
          return afterFill || page.ready;
        }, fill => {
          inserting = true;
          try {return fill();} finally {inserting = false;}
        }, message.allowInterrupted === true);
      } finally {sending = false;}
    })().then(respond, () => respond({outcome: 'unknown', reason: 'unsupported_page', ready: false}));
    return true;
  });
})();
