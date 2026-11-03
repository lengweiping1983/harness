import {beginNativeInput} from './native-input.mjs'
import {NativeViewports} from './native-viewport.mjs'
import {PageReadiness} from './page-readiness.mjs'
import {ClosedTabs} from './closed-tabs.mjs'
import {TabSelections} from './tab-selections.mjs'
import {websiteMenu} from './context-menu.mjs'
import path from 'node:path'
import {mkdir} from 'node:fs/promises'
import {WebsitePermissions} from './permissions.mjs'
import {NativeDialogs} from './dialogs.mjs'
import {NativeBrowserError} from './browser-errors.mjs'
import {PageFind} from './find.mjs'
import {browserShortcut, attachMenuShortcutGuard} from './shortcuts.mjs'
import {TabHealth} from './tab-health.mjs'
import {websiteURL, viewBounds} from './browser-policy.mjs'
import {fileURLToPath} from 'node:url'

// 浏览器状态随主进程存活；原生监听在窗口可用后由 attach 一次性注册。
export function createBrowserRuntime({app, WebContentsView, ipcMain, session, shell, Menu, clipboard, systemPermissions, getWindow, getDownloads, getBackend, getOrigin, getMenuText = () => null}) {
  let activeSession = 'desktop'
  const owners = new Map()
  const controllers = new Map()
  const automation = new Set()
  const targets = new Map()
  const readiness = new PageReadiness()
  const nativeViewports = new NativeViewports()
  const health = new Map()
  const selections = new TabSelections()
  const finds = new Map()
  const tabs = new Map()
  const closedTabs = new ClosedTabs()
  let selected = null
  let bounds = null
  let visible = false
  let screenshotActive = false
  let permissionRevision = 0
  const dialogs = new NativeDialogs(() => publish())
  const permissions = new WebsitePermissions(() => publish())
  function pageViewport(view) {
    return nativeViewports.peek(view)
  }
  function state() {
    return {session_id: activeSession, can_reopen: closedTabs.has(activeSession) && [...owners.values()].filter(value => value === activeSession).length < 12, dialog: dialogs.snapshot(selected), find: finds.get(selected)?.state || {open:false, text:'', matches:0, ordinal:0}, downloads: getDownloads()?.snapshot(activeSession) || [], permissions: permissions.snapshot(selected, tabs.get(selected)?.webContents.getURL()), owner: controllers.get(activeSession) || 'agent', active_tab: selected, tabs: [...tabs].filter(([id]) => owners.get(id) === activeSession).map(([id, view]) => ({id, target_id:targets.get(id), viewport:pageViewport(view), zoom: view.webContents.getZoomFactor(), url: health.get(id)?.url || view.webContents.getURL(), issue: health.get(id)?.issue, title: view.webContents.getTitle(), loading: view.webContents.isLoading(), can_go_back: view.webContents.navigationHistory.canGoBack(), can_go_forward: view.webContents.navigationHistory.canGoForward()}))}
  }
  function selectTab(id) {
    const owner = owners.get(id)
    selections.set(owner, id)
    if (owner === activeSession) {if (selected !== id) permissionRevision++; selected = id}
    readiness.changed()
  }
  function forgetTab(id) {
    if (!owners.has(id)) return
    const owner = owners.get(id)
    dialogs.clear(id)
    nativeViewports.forget(id)
    finds.delete(id)
    tabs.delete(id); owners.delete(id); targets.delete(id); health.delete(id)
    permissions.clear(id)
    selections.forget(owner, id, [...tabs.keys()].filter(key => owners.get(key) === owner))
    if (selected === id) selected = selections.get(activeSession) ?? null
    readiness.finish(id)
    layout(); publish()
  }
  let browserKeyboard = false
  function focusAddress() {getWindow().webContents.focus(); getWindow().webContents.send('browser-focus-address')}
  function handleShortcut(event, input, native = false) {
    const shortcut = browserShortcut(input)
    if (!shortcut || !visible || (!native && !browserKeyboard && shortcut.action !== 'focus_address')) return
    if (shortcut.action === 'find_close' && !finds.get(selected)?.state.open) return
    event.preventDefault()
    if (input.isAutoRepeat) return
    controllers.set(activeSession, 'user')
    const owner = activeSession
    void browserAction(shortcut.action, shortcut).catch(error => publishError(owner, error))
  }
  function publish() { if (getWindow() && !getWindow().isDestroyed()) getWindow().webContents.send('browser-state', state()) }
  function publishError(owner, error) {
    const window = getWindow()
    if (window && !window.isDestroyed()) window.webContents.send('browser-error', {session_id: owner, message: error.message, ...(error instanceof NativeBrowserError ? {code:error.code, copy:error.copy} : {})})
  }
  function layout() {
    if (!getWindow() || getWindow().isDestroyed()) return
    const size = getWindow().getContentSize()
    const area = bounds ? viewBounds(bounds, size) : null
    const usable = area && area.width > 0 && area.height > 0
    for (const [id, view] of tabs) {
      view.setVisible(Boolean(!screenshotActive && visible && usable && id === selected && owners.get(id) === activeSession && !health.get(id)?.issue))
      if (usable) view.setBounds(area)
      else if (view.getBounds().width <= 0 || view.getBounds().height <= 0) view.setBounds(viewBounds({x:0, y:0, width:1024, height:768}, size))
    }
  }
  async function create(url = 'about:blank', owner = activeSession, replacing = null, options = {}) {
    if (!replacing && [...owners.values()].filter(value => value === owner).length >= 12) throw new NativeBrowserError('tabLimit', {count:12})
    if (url !== 'about:blank') url = websiteURL(url)
    const view = options.view || new WebContentsView({webPreferences: {partition: 'persist:harness-browser', preload: fileURLToPath(new URL('./browser-preload.cjs', import.meta.url)), nodeIntegration: false, contextIsolation: true, sandbox: true}})
    const wc = view.webContents
    const id = String(wc.id)
    const prior = selections.get(owner)
    try {
      const status = new TabHealth(url)
      health.set(id, status)
      tabs.set(id, view)
      finds.set(id, new PageFind(wc, () => publish()))
      owners.set(id, owner)
      readiness.begin(id)
      wc.once('destroyed', () => readiness.finish(id))
      dialogs.watch(id, wc)
      getWindow().contentView.addChildView(view)
      if (!options.background) selectTab(id)
      const takeControl = () => {if (!automation.has(owner)) {controllers.set(owner, 'user'); publish()}}
      attachMenuShortcutGuard(wc)
      wc.on('before-input-event', (event, input) => {if (id === selected && owner === activeSession) handleShortcut(event, input, true)})
      wc.on('before-input-event', (_event, input) => {if (input.type === 'keyDown') takeControl()})
      wc.on('before-mouse-event', (_event, mouse) => {if (['mouseDown', 'mouseWheel'].includes(mouse.type)) takeControl()})
      wc.on('destroyed', () => {
        if ((closing || confirmedClose) && owners.has(id)) closedTabs.remember(owner, closeURL)
        const wasSelected = selected === id
        if (!getWindow().isDestroyed()) getWindow().contentView.removeChildView(view)
        forgetTab(id)
        if (wasSelected && !getWindow().isDestroyed()) {if (selected) tabs.get(selected)?.webContents.focus(); else focusAddress()}
      })
      let documentRevision = 0
      let closeURL = null
      let closing = false
      let confirmedClose = false
      let navigation = null
      let confirmedNavigation = false
      wc.on('will-prevent-unload', event => {
        if (confirmedClose) {event.preventDefault(); return}
        if (confirmedNavigation) {confirmedNavigation = false; event.preventDefault(); return}
        if (!closing && !navigation) return
        const closeRequested = closing
        const retry = navigation
        navigation = null
        closing = false
        if (owner === activeSession) {selectTab(id); layout()}
        dialogs.open(id, 'beforeunload', '', wc.getURL(), '', accept => {
          if (accept && !wc.isDestroyed()) {if (closeRequested) {confirmedClose = true; wc.close({waitForBeforeUnload:true})} else {confirmedNavigation = true; void retry().catch(error => publishError(owner, error))}}
        }, {key:closeRequested ? 'browser.desktop.confirmClose' : 'browser.desktop.confirmLeave', params:{}})
      })
      view.navigate = async operation => {
        if (dialogs.snapshot(id)) throw new NativeBrowserError('navigationBlocked')
        confirmedNavigation = false
        navigation = operation
        try {await operation()} catch (error) {if (!dialogs.snapshot(id)) navigation = null; throw error}
      }
      view.requestClose = () => {if (!closing && !dialogs.snapshot(id)) {closeURL = wc.getURL() || status.url; closing = true; wc.close({waitForBeforeUnload:true})}}

      wc.on('context-menu', (_event, params) => {
        if (id !== selected || owner !== activeSession || !visible || dialogs.snapshot(id)) return
        const labels = getMenuText()
        if (!labels) return
        const revision = documentRevision
        const valid = () => !wc.isDestroyed() && selected === id && activeSession === owner && documentRevision === revision
        const template = websiteMenu(params, {
          open: url => {if (valid()) void create(url, owner).catch(error => publishError(owner, error))},
          inspect: (x, y) => {if (valid() && Number.isInteger(x) && Number.isInteger(y)) {wc.openDevTools({mode:'detach'}); wc.inspectElement(x, y)}},
          copyLink: url => {if (valid()) clipboard.writeText(url)},
          edit: action => {if (valid()) {controllers.set(owner, 'user'); wc.focus(); wc[action](); publish()}}
        }, key => labels[key])
        if (template.length) Menu.buildFromTemplate(template).popup({window:getWindow(), frame:params.frame})
      })
      wc.setWindowOpenHandler(details => {
        let destination
        try {destination = details.url === 'about:blank' ? details.url : websiteURL(details.url)} catch {return {action:'deny'}}
        if ([...owners.values()].filter(value => value === owner).length >= 12) return {action:'deny'}
        return {action:'allow', overrideBrowserWindowOptions:{webPreferences:{nodeIntegration:false, nodeIntegrationInWorker:false, nodeIntegrationInSubFrames:false, webviewTag:false, contextIsolation:true, sandbox:true, preload:fileURLToPath(new URL('./browser-preload.cjs', import.meta.url))}}, createWindow: childOptions => {
          const supplied = {...childOptions}
          // Electron's deferred background creation supplies an unusable empty handle.
          if (!supplied.webContents) delete supplied.webContents
          const child = new WebContentsView({...supplied, webPreferences:{...childOptions.webPreferences, session:wc.session, preload:fileURLToPath(new URL('./browser-preload.cjs', import.meta.url)), nodeIntegration:false, nodeIntegrationInWorker:false, nodeIntegrationInSubFrames:false, webviewTag:false, contextIsolation:true, sandbox:true}})
          void create(destination, owner, null, {view:child, popup:true, background:details.disposition === 'background-tab', manualLoad:details.disposition === 'background-tab' && !supplied.webContents, loadOptions:{httpReferrer:details.referrer, ...(details.postBody ? {postData:details.postBody.data, extraHeaders:'Content-Type: '+details.postBody.contentType+(details.postBody.boundary ? '; boundary='+details.postBody.boundary : '')} : {})}}).catch(error => {
            if (!child.webContents.isDestroyed()) child.webContents.close()
            publishError(owner, error)
          })
          return child.webContents
        }}
      })
      wc.on('will-navigate', (event, url) => {try {websiteURL(url)} catch {event.preventDefault()}})
      for (const event of ['did-navigate', 'did-navigate-in-page', 'page-title-updated', 'did-start-loading', 'did-stop-loading']) wc.on(event, publish)
      wc.on('did-navigate', () => {navigation = null; confirmedNavigation = false})
      wc.on('did-stop-loading', () => {if (!status.issue) status.navigating(wc.getURL()); publish()})
      wc.on('did-start-navigation', (_event, url, _inPlace, mainFrame) => {if (mainFrame) {documentRevision++; finds.get(id)?.close(); permissions.clear(id); status.navigating(url); layout(); publish()}})
      wc.on('did-fail-load', (_event, code, _description, _url, mainFrame) => {if (mainFrame && code !== -3) {navigation = null; confirmedNavigation = false}; status.failed(code, mainFrame); layout(); publish()})
      wc.on('render-process-gone', (_event, details) => {navigation = null; confirmedNavigation = false; closing = false; dialogs.clear(id); finds.get(id)?.close(); permissions.clear(id); status.gone(details.reason); controllers.set(owner, 'user'); layout(); publish()})
      wc.on('unresponsive', () => {status.unresponsive(); controllers.set(owner, 'user'); layout(); publish()})
      wc.on('responsive', () => {status.responsive(); layout(); publish()})
      layout()
      try {
        if (options.popup) await new Promise(resolve => setImmediate(resolve))
        if (options.manualLoad) {
          try {await wc.loadURL(url, options.loadOptions)} catch (error) {if (!status.issue && error.code !== 'ERR_ABORTED') throw error}
        }
        if (owners.get(id) !== owner || wc.isDestroyed()) throw new NativeBrowserError('tabClosed')
        wc.debugger.attach('1.3')
        try {
          const info = await wc.debugger.sendCommand('Target.getTargetInfo')
          if (owners.get(id) !== owner || wc.isDestroyed()) throw new NativeBrowserError('tabClosed')
          targets.set(id, info.targetInfo.targetId)
        } finally {if (!wc.isDestroyed() && wc.debugger.isAttached()) wc.debugger.detach()}
      } finally {readiness.finish(id)}
      try {if (!options.popup) await wc.loadURL(url)} catch (error) {if (!status.issue && error.code !== 'ERR_ABORTED') throw error} finally {publish()}
      if (owners.get(id) !== owner || wc.isDestroyed()) throw new NativeBrowserError('tabClosed')
      return {...state(), target_id: targets.get(id)}
    } catch (error) {
      const restore = selections.get(owner) === id
      if (!wc.isDestroyed()) wc.close()
      forgetTab(id)
      if (restore && tabs.has(prior) && owners.get(prior) === owner) {selectTab(prior); layout(); publish()}
      throw error
    }
  }
  async function bridgeAction(args) {
    if (!/^[a-zA-Z0-9_-]+$/.test(args.session_id || '')) throw new NativeBrowserError('invalidSession')
    if (args.action === 'begin' || args.action === 'state' && args.measure !== false) await readiness.waitSelected(() => selections.get(args.session_id), id => !tabs.has(id) || targets.has(id))
    const own = () => [...tabs].filter(([id]) => owners.get(id) === args.session_id)
    const snapshot = async (measure = true) => {
      const selectedId = selections.get(args.session_id)
      const owner = controllers.get(args.session_id) || 'agent'
      const entries = own().filter(([id]) => targets.has(id))
      const result = tabs => ({owner, tabs, active_target:targets.get(selectedId) ?? null, dialogs:entries.flatMap(([id]) => {const entry = dialogs.snapshot(id); return entry ? [{...entry, page_id:targets.get(id)}] : []})})
      if (!measure) return result(entries.map(([id, view]) => ({id, target_id:targets.get(id), viewport:nativeViewports.peek(view)})))
      const measured = await Promise.all(entries.map(async ([id, view]) => ({id, target_id:targets.get(id), viewport:await nativeViewports.get(view, {blocked:id !== selectedId || Boolean(dialogs.snapshot(id) || health.get(id)?.issue)})})))
      if (selectedId !== selections.get(args.session_id) || owner !== (controllers.get(args.session_id) || 'agent') || measured.some(tab => targets.get(tab.id) !== tab.target_id || owners.get(tab.id) !== args.session_id)) {
        throw new NativeBrowserError('pageChanged')
      }
      return result(measured)
    }
    switch (args.action) {
      case 'dialog_response': {const found = own().find(([id]) => dialogs.snapshot(id)?.id === args.id); if (!found) throw new NativeBrowserError('inactive'); await dialogs.respond(found[0], found[1].webContents, args); return snapshot(false)}
      case 'begin': if ((controllers.get(args.session_id) || 'agent') !== args.actor) throw new NativeBrowserError('controlChanged'); return beginNativeInput(automation, args.session_id, snapshot, state => (controllers.get(args.session_id) || 'agent') === args.actor && state.active_target === (targets.get(selections.get(args.session_id)) ?? null))
      case 'end': automation.delete(args.session_id); return snapshot(false)
      case 'control': if (!['user', 'agent'].includes(args.owner)) throw new NativeBrowserError('invalidOwner'); controllers.set(args.session_id, args.owner); publish(); return snapshot(false)
      case 'state': return snapshot(args.measure !== false)
      case 'new_tab': {
        return create('about:blank', args.session_id)
      }
      case 'select_target': {
        const found = own().find(([id]) => targets.get(id) === args.target_id)
        if (!found) throw new NativeBrowserError('targetMissing')
        selectTab(found[0]); layout(); publish()
        return snapshot(false)
      }
      case 'close_session':
        for (const [id, view] of own()) {getWindow().contentView.removeChildView(view); view.webContents.close(); forgetTab(id)}
        closedTabs.clear(args.session_id)
        controllers.delete(args.session_id); automation.delete(args.session_id); selections.delete(args.session_id)
        publish(); return snapshot(false)
      default: throw new NativeBrowserError('bridgeAction')
    }
  }
  function attach() {
    const browserSession = session.fromPartition('persist:harness-browser')
    browserSession.on('will-download', (event, item, wc) => {
      const owner = owners.get(String(wc?.id))
      if (!owner) {event.preventDefault(); return}
      try {getDownloads().add(item, owner)} catch (error) {console.error('Browser download failed:', error.message)}
    })
    browserSession.setPermissionCheckHandler((wc, permission, requestingOrigin, details) => {
      if (!wc || wc.isDestroyed() || !tabs.has(String(wc.id))) return false
      if (permission === 'media' && systemPermissions && (details.mediaTypes || [details.mediaType]).some(type => systemPermissions.status(type === 'audio' ? 'microphone' : 'camera') !== 'granted')) return false
      return permissions.check(String(wc.id), wc.getURL(), details.requestingUrl || requestingOrigin, permission, details)
    })
    browserSession.setPermissionRequestHandler((wc, permission, callback, details) => {
      const id = String(wc?.id)
      if (!wc || wc.isDestroyed() || id !== selected || !visible || owners.get(id) !== activeSession) {callback(false); return}
      const owner = activeSession
      const revision = permissionRevision
      let current = true, finished = false
      const invalidate = () => {current = false}
      const events = ['did-start-navigation','render-process-gone','destroyed']
      events.forEach(name => wc.on(name, invalidate))
      const valid = () => current && revision === permissionRevision && !screenshotActive && !wc.isDestroyed() && tabs.has(id) && id === selected && visible && owners.get(id) === activeSession && owner === activeSession
      const finish = allowed => {if (finished) return; finished = true; events.forEach(name => wc.removeListener(name, invalidate)); callback(allowed)}
      const report = (copy, cause) => {
        if (!valid()) return
        const error = new NativeBrowserError('systemPermission', {}, {cause})
        error.copy = copy ?? error.copy
        publishError(owner, error)
      }
      permissions.request(id, wc.getURL(), permission, allow => {
        if (!allow || !valid() || permission !== 'media' || !systemPermissions) {finish(allow && valid()); return}
        void (async () => {
          for (const type of details.mediaTypes || [details.mediaType]) {
            if (!valid()) {finish(false); return}
            const result = await systemPermissions.request(type === 'audio' ? 'microphone' : 'camera')
            if (!result.granted) {
              report(result.copy)
              finish(false); return
            }
          }
          finish(valid())
        })().catch(error => {report(error.copy, error); finish(false)})
      }, details)
    })
    ipcMain.on('website-viewport-changed', event => {
      const id = String(event.sender.id)
      if (tabs.has(id) && event.senderFrame === event.sender.mainFrame) nativeViewports.invalidate(id)
    })
    ipcMain.on('website-prompt', (event, message, defaultValue) => {
      const id = String(event.sender.id)
      const frame = event.senderFrame
      if (!tabs.has(id) || !frame || typeof message !== 'string' || typeof defaultValue !== 'string' || message.length > 4000 || defaultValue.length > 2000) {event.returnValue = null; return}
      dialogs.open(id, 'prompt', message, frame.url, defaultValue, (accept, text) => {event.returnValue = accept ? text : null})
    })
    ipcMain.handle('browser', async (event, action, args) => {
      if (event.sender !== getWindow().webContents || event.senderFrame !== getWindow().webContents.mainFrame || new URL(event.senderFrame.url).origin !== getOrigin()) throw new NativeBrowserError('untrusted')
      try {return await browserAction(action, args)} catch (error) {
        if (error instanceof NativeBrowserError) return {failure:{code:error.code, copy:error.copy}}
        throw error
      }
    })
    getWindow().webContents.on('before-input-event', handleShortcut)
    getWindow().on('resize', layout)
    getWindow().on('move', () => nativeViewports.clear())
  }
  async function browserAction(action, args = {}) {
    if (action !== 'bind' && args.session_id !== undefined && args.session_id !== activeSession) throw new NativeBrowserError('sessionChanged')
    const wc = tabs.get(selected)?.webContents
    switch (action) {
      case 'dialog_response': controllers.set(activeSession, 'user'); await dialogs.respond(selected, wc, args); break
      case 'zoom': {
        if (wc) {
          if (![0, 1, -1].includes(args.direction)) throw new NativeBrowserError('zoomInvalid')
          const levels = [.5, .67, .8, .9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3]
          const current = wc.getZoomFactor()
          const factor = args.direction === 0 ? 1 : args.direction > 0 ? levels.find(value => value > current + .01) ?? 3 : levels.findLast(value => value < current - .01) ?? .5
          wc.setZoomFactor(factor)
        }
        break
      }
      case 'find_open': if (wc) {finds.get(selected).open(); getWindow().webContents.focus(); getWindow().webContents.send('browser-focus-find')}; return state()
      case 'find': if (wc) finds.get(selected).search(args.text, args.forward !== false, args.next === true); return state()
      case 'find_next': if (wc && finds.get(selected).state.text) finds.get(selected).search(finds.get(selected).state.text, args.forward !== false, true); else return browserAction('find_open'); return state()
      case 'find_close': finds.get(selected)?.close(); wc?.focus(); return state()
      case 'keyboard_context': browserKeyboard = args.active === true; return state()
      case 'focus_address': focusAddress(); return state()
      case 'reopen_tab': {
        const owner = activeSession
        if ([...owners.values()].filter(value => value === owner).length >= 12) throw new NativeBrowserError('tabLimit', {count:12})
        const url = closedTabs.take(owner)
        if (url) {
          const generation = closedTabs.capture(owner)
          try {return await create(url, owner)} catch (error) {if (closedTabs.isCurrent(owner, generation)) closedTabs.remember(owner, url); throw error}
        }
        return state()
      }
      case 'close_current_tab': if (selected) return browserAction('close_tab', {id:selected}); return state()
      case 'cycle_tab':
      case 'index_tab': {
        const ids = [...tabs.keys()].filter(id => owners.get(id) === activeSession)
        if (!ids.length) return state()
        const index = action === 'cycle_tab' ? (ids.indexOf(selected) + args.offset + ids.length) % ids.length : args.index === 8 ? ids.length - 1 : args.index
        if (ids[index]) {selectTab(ids[index]); layout(); tabs.get(selected).webContents.focus()}
        break
      }
      case 'browser_info': return {...state(), info:{chromium:process.versions.chrome, electron:process.versions.electron, downloads:path.join(app.getPath('downloads'), 'Harness')}}
      case 'downloads_folder': {const directory = path.join(app.getPath('downloads'), 'Harness'); await mkdir(directory, {recursive:true}); const error = await shell.openPath(directory); if (error) throw Error(error); return state()}
      case 'service_state': return {status:getBackend().status}
      case 'restart_backend': await getBackend().start(); return {status:getBackend().status}
      case 'control': if (!['user', 'agent'].includes(args.owner)) throw new NativeBrowserError('invalidOwner'); controllers.set(activeSession, args.owner); publish(); return state()
      case 'bind': if (!/^[a-zA-Z0-9_-]+$/.test(args.session_id || '')) throw new NativeBrowserError('invalidSession'); if (activeSession !== args.session_id) permissionRevision++; activeSession = args.session_id; selected = tabs.has(selections.get(activeSession)) ? selections.get(activeSession) : [...tabs.keys()].find(id => owners.get(id) === activeSession) ?? null; layout(); publish(); return state()
      case 'download_action': {const destination = getDownloads().action(activeSession, args.id, args.action); if (destination) shell.showItemInFolder(destination); break}
      case 'permission_answer': permissions.answer(selected, wc?.getURL(), args.id, args.allow); break
      case 'permission_reset': permissionRevision++; permissions.clear(selected); wc?.reload(); break
      case 'state': return state()
      case 'layout': bounds = viewBounds(args.bounds, getWindow().getContentSize()); if (visible !== Boolean(args.visible)) permissionRevision++; visible = Boolean(args.visible); layout(); break
      case 'new_tab': {const result = await create(args.url); if (!args.url) focusAddress(); return result}
      case 'select_tab': if (!tabs.has(args.id) || owners.get(args.id) !== activeSession) throw new NativeBrowserError('tabMissing'); selectTab(args.id); layout(); break
      case 'close_tab': {
        const view = tabs.get(args.id)
        if (!view || owners.get(args.id) !== activeSession) throw new NativeBrowserError('tabMissing')
        view.requestClose(); break
      }
      case 'navigate': controllers.set(activeSession, 'user'); if (!wc) return create(args.url); try {await tabs.get(selected).navigate(() => wc.loadURL(websiteURL(args.url)))} catch (error) {if (!health.get(String(wc.id))?.issue && error.code !== 'ERR_ABORTED') throw error} break
      case 'back': if (wc?.navigationHistory.canGoBack()) await tabs.get(selected).navigate(async () => wc.navigationHistory.goBack()); break
      case 'forward': if (wc?.navigationHistory.canGoForward()) await tabs.get(selected).navigate(async () => wc.navigationHistory.goForward()); break
      case 'reload':
        if (wc) {
          const status = health.get(selected)
          controllers.set(activeSession, 'user')
          if (['crashed', 'closed', 'unresponsive'].includes(status?.issue)) {
            const old = selected
            const oldView = tabs.get(old)
            if (status.issue === 'unresponsive') wc.forcefullyCrashRenderer()
            await create(status.url, owners.get(old), old)
            getWindow().contentView.removeChildView(oldView)
            wc.close()
            forgetTab(old)
          }
          else if (status?.issue) {
            status.navigating(status.url); layout()
            try {await wc.loadURL(status.url)} catch (error) {
              if (error.code !== 'ERR_ABORTED') {status.failed(error.errno ?? -2, true); throw error}
            } finally {layout(); publish()}
          }
          else await tabs.get(selected).navigate(async () => wc.reload())
        }
        break
      case 'stop': wc?.stop(); break
      default: throw new NativeBrowserError('actionInvalid')
    }
    publish(); return state()
  }
  return {
    bridgeAction, attach, publish,
    suspend() {permissionRevision++; screenshotActive = true; layout()},
    resume() {screenshotActive = false; layout()},
    close() {
      for (const view of tabs.values()) if (!view.webContents.isDestroyed()) view.webContents.close()
      tabs.clear()
      closedTabs.clearAll()
    },
  }
}
