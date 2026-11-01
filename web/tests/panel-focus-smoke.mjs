import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

await browserScenario(async ({server, browser}) => {
  const page = await browser.newPage()
  await page.goto(server.resolvedUrls.local[0])
  const result = await page.evaluate(async () => {
    const {focusPanel} = await import('/src/lib/panelFocus.ts')
    const shell = document.createElement('div')
    shell.innerHTML = '<button id="invoker">open</button><section><button>first</button><button>selected</button></section>'
    document.body.append(shell)
    const [invoker, first, selected] = shell.querySelectorAll('button')
    const panel = shell.querySelector('section')
    invoker.focus()
    focusPanel(panel)
    const initial = document.activeElement === first
    selected.focus()
    // Model a callback delayed until after the user has navigated inside the drawer.
    await new Promise(resolve => requestAnimationFrame(resolve))
    focusPanel(panel)
    const retained = document.activeElement === selected
    focusPanel(null)
    shell.remove()
    return {initial, retained}
  })
  assert.deepEqual(result, {initial:true, retained:true})
  const race = await page.evaluate(async () => {
    const {createPanelFocusFrames} = await import('/src/lib/panelFocus.ts')
    const shell = document.createElement('div')
    shell.innerHTML = '<button id="origin">origin</button><section><button>first</button><button>selected</button></section>'
    document.body.append(shell)
    const [origin, first, selected] = shell.querySelectorAll('button')
    const panel = shell.querySelector('section')
    const originalRequest = window.requestAnimationFrame, originalCancel = window.cancelAnimationFrame
    const queued = new Map();let serial = 0
    window.requestAnimationFrame = callback => {queued.set(++serial,callback);return serial}
    window.cancelAnimationFrame = id => queued.delete(id)
    const flush = () => {const callbacks=[...queued.values()];queued.clear();callbacks.forEach(callback=>callback(0))}
    const frames = createPanelFocusFrames()
    try {
      frames.open(panel);flush()
      const initial = document.activeElement === first
      frames.close(()=>origin.focus())
      frames.open(panel)
      selected.focus();flush()
      const retained = document.activeElement === selected
      frames.close(()=>origin.focus());flush()
      const restored = document.activeElement === origin
      frames.open(panel);frames.dispose();flush()
      const disposed = document.activeElement === origin
      frames.close(()=>first.focus());flush()
      return {initial,retained,restored,disposed,noLateRestore:document.activeElement===origin}
    } finally {
      frames.dispose();window.requestAnimationFrame=originalRequest;window.cancelAnimationFrame=originalCancel;shell.remove()
    }
  })
  assert.deepEqual(race,{initial:true,retained:true,restored:true,disposed:true,noLateRestore:true})
  await page.close()
  console.log('Drawer focus: initial focus and preservation of user navigation across deferred initialization passed')
}, {entry:'/src/lib/panelFocus.ts'})
