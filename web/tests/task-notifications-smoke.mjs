import {fileURLToPath} from 'node:url'
import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ProjectSidebar} from '/src/components/sidebar/ProjectSidebar.tsx'
import {Toaster} from '/src/components/overlays/Toaster.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useSessionActivity,observeSessionCompletion,useSessionActivityStore} from '/src/store/sessionActivity.ts'
import {useSessionNotificationStore,prepareTaskNotifications} from '/src/store/sessionNotifications.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.copy=t;window.locale=setLocale;window.app=useAppStore;window.unread=useSessionNotificationStore
window.received=0;useSessionActivityStore.subscribe(()=>window.received++);window.sent=[];window.cleared=[];window.refreshes=0;window.permissionRequests=0;window.webNotifications=[]
window.harnessDesktop={notifyTask:async payload=>{window.sent.push(payload);return true},clearTaskNotification:async id=>window.cleared.push(id),onTaskNotificationOpen:listener=>{window.openNotification=listener;return()=>{window.openNotification=null}}}
class FakeNotification {
 static permission='granted'
 static async requestPermission(){window.permissionRequests++;return 'denied'}
 constructor(title,options){this.title=title;this.options=options;window.webNotifications.push(this)}
 close(){this.onclose?.()}
}
window.Notification=FakeNotification
window.prepareNotifications=prepareTaskNotifications
window.visible=true;window.focused=true
Object.defineProperty(document,'hidden',{configurable:true,get:()=>!window.visible})
document.hasFocus=()=>window.focused
window.read=()=>window.dispatchEvent(new Event('focus'))
window.poll=()=>document.dispatchEvent(new Event('visibilitychange'))
window.live=(id,run,status='completed')=>observeSessionCompletion(id,{type:'run/state',id:run,status,started_at:1})
useAppStore.setState({booted:true,sessionsLoading:false,workspacesLoading:false,workspaces:[],activeSessionId:'a',sessions:[{id:'a',title:'Current 原文',created_at:1},{id:'b',title:'Background 原文',created_at:1}],refreshSessions:async()=>{window.refreshes++;return true}})
function Fixture(){const [shown,setShown]=useState(true);window.setShown=setShown;window.shown=shown;useSessionActivity(shown);return React.createElement(React.Fragment,null,React.createElement(ProjectSidebar),React.createElement(Toaster))}
const root=createRoot(document.getElementById('root'));window.mount=shown=>root.render(shown?React.createElement(Fixture):null);window.mount(true)
`
await browserScenario(async ({server,browser}) => {
  const page = await browser.newPage(), errors = []
  page.on('pageerror', error => errors.push(error.message))
  let cursor = 0, serverId = 'fixture-server', completions = [], requests = 0, held
  const completed = (session_id, status = 'completed') => {const row = {seq: ++cursor, session_id, run_id: 'run-' + cursor, status}; completions.push(row); return row}
  await page.route('**/api/v1/sessions/activity*', async route => {
    requests++
    let heldComplete
    if (held) {const wait = held; held = undefined; heldComplete=wait.complete; await wait.promise}
    const query = new URL(route.request().url()).searchParams
    try {await route.fulfill({json: {running: [], catalog_revision: 0, server_id: serverId, completion_cursor: cursor, completions_lost: false,
      completions: query.get('server_id') === serverId ? completions.filter(row => row.seq > Number(query.get('after'))) : []}})} finally {heldComplete?.()}
  })
  const poll = async (processResponse = true) => {
    const before = requests
    const received = await page.evaluate(() => window.received)
    await page.evaluate(() => window.poll())
    const limit = Date.now() + 10000
    while (requests <= before && Date.now() < limit) await new Promise(resolve => setTimeout(resolve, 10))
    assert.ok(requests > before, 'activity request observed')
    if (processResponse) await page.waitForFunction(previous => window.received > previous, received)
  }
  await page.goto(server.resolvedUrls.local[0] + '__task-notifications')
  await page.waitForFunction(() => window.openNotification && window.refreshes > 0)
  for (const language of ['en', 'zh-CN', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    await page.setViewportSize({width, height: 900})
    await page.evaluate(async ({language,theme}) => {
      await window.locale(language); document.documentElement.className=theme
      window.visible=true;window.focused=true;window.app.getState().selectSession('a');window.app.setState({toasts:[]})
    }, {language,theme})
    const prior = await page.evaluate(() => window.sent.length)
    completed('a'); await poll()
    await page.waitForFunction(() => window.app.getState().toasts.length === 0)
    assert.equal(await page.evaluate(() => window.sent.length), prior)
    const row = completed('b'); await poll()
    await page.waitForFunction(expected => window.sent.length === expected, prior + 1)
    const unread = await page.evaluate(() => window.copy('ui.notifications.unread'))
    const dot = page.getByRole('img', {name: unread, exact: true})
    await dot.waitFor()
    assert.notEqual(await dot.evaluate(element => getComputedStyle(element).backgroundColor), 'rgba(0, 0, 0, 0)')
    assert.equal(await page.evaluate(() => window.sent.at(-1).body), 'Background 原文')
    const open = await page.evaluate(() => window.copy('ui.notifications.open'))
    await page.getByRole('button', {name: open, exact: true}).click()
    await dot.waitFor({state: 'detached'})
    assert.equal(await page.evaluate(() => window.app.getState().activeSessionId), 'b')
    await poll()
    assert.equal(await page.evaluate(() => window.sent.length), prior + 1, row.run_id + ' notified once')
    // 当前实时终态已经看过，随后切走也不能被快照重新标成未读。
    const seen = completed('b')
    await page.evaluate(row => {window.live('b',row.run_id);window.app.getState().selectSession('a')}, seen)
    await poll()
    assert.equal(await page.evaluate(() => window.sent.length), prior + 1)
    assert.equal(await dot.count(), 0)
    const screenshot = new URL('../../output/codex-notifications/' + language + '-' + theme + '-' + width + '.png', import.meta.url)
    // 截图保留未读蓝点和通知的真实布局。
    completed('b'); await poll()
    await page.waitForFunction(expected => window.sent.length === expected, prior + 2)
    await page.screenshot({path: fileURLToPath(screenshot)})
    await page.getByRole('button', {name: 'Background 原文', exact: true}).press('Enter')
    await dot.waitFor({state:'detached'})
  }
  // 当前对话被隐藏或失焦仍通知；恢复可见会清除未读。
  for (const state of ['hidden', 'blurred', 'covered']) {
    const before = await page.evaluate(() => window.sent.length)
    await page.evaluate(state => {window.app.getState().selectSession('a');window.visible=state!=='hidden';window.focused=state!=='blurred';window.setShown(state!=='covered')}, state)
    await page.waitForFunction(state => window.shown === (state !== 'covered'), state)
    completed('a', 'failed'); await poll()
    await page.waitForFunction(count => window.sent.length === count, before + 1)
    assert.equal(await page.getByRole('img', {name: await page.evaluate(() => window.copy('ui.notifications.unread')), exact:true}).count(), 0)
    await page.evaluate(() => {window.visible=true;window.focused=true;window.setShown(true);window.read()})
    await page.waitForFunction(() => !window.unread.getState().unread.includes('a'))
  }
  // 重启不重发历史；通知点击仍进入原对话。
  const beforeRestart = await page.evaluate(() => window.sent.length)
  serverId='restarted';cursor=0;completions=[];completed('b');await poll()
  assert.equal(await page.evaluate(() => window.sent.length), beforeRestart)
  completed('b','interrupted');await poll()
  await page.waitForFunction(count => window.sent.length === count, beforeRestart+1)
  await page.evaluate(() => window.openNotification('b'))
  await page.waitForFunction(() => window.app.getState().activeSessionId==='b' && window.unread.getState().unread.length===0)
  // 纯 Web 已授权、拒绝授权和不支持通知均保留页面通知与蓝点。
  await page.evaluate(() => {delete window.harnessDesktop;window.app.getState().selectSession('a')})
  for (const permission of ['granted','denied','unsupported']) {
    await page.evaluate(permission => {if(permission==='unsupported')delete window.Notification;else window.Notification.permission=permission},permission)
    completed('b');await poll()
    await page.waitForFunction(() => window.unread.getState().unread.includes('b'))
    if (permission==='granted') {
      assert.equal(await page.evaluate(() => window.webNotifications.at(-1).options.body),'Background 原文')
      await page.evaluate(() => window.webNotifications.at(-1).onclick())
    } else await page.getByRole('button',{name:'Background 原文',exact:true}).click()
    await page.waitForFunction(() => !window.unread.getState().unread.includes('b'))
    await page.evaluate(() => window.app.getState().selectSession('a'))
  }
  // 卸载后的旧请求不得通知或改写替换实例。
  let release, finish
  const finished = new Promise(resolve=>{finish=resolve})
  held={promise:new Promise(resolve=>{release=resolve}),complete:finish}
  const beforeUnmount = await page.evaluate(()=>({received:window.received,toasts:window.app.getState().toasts.length}))
  await poll(false);await page.evaluate(()=>window.mount(false));completed('b');release();await finished
  assert.deepEqual(await page.evaluate(()=>({received:window.received,toasts:window.app.getState().toasts.length})),beforeUnmount)
  await page.evaluate(()=>window.mount(true))
  await page.waitForFunction(count=>window.received>count,beforeUnmount.received)
  assert.equal(await page.evaluate(()=>window.app.getState().toasts.length),beforeUnmount.toasts)
  assert.deepEqual(errors,[])
  await page.close()
  console.log('Task notifications: real activity hook, sidebar, toast, visibility/focus, click/read, live/poll dedup, restart, browser permission fallbacks and language/theme/width matrix passed')
}, {entry:'/__task-notifications-entry.js',vite:{plugins:[{name:'task-notifications-fixture',resolveId(id){if(id==='/__task-notifications-entry.js')return id},load(id){if(id==='/__task-notifications-entry.js')return entry},configureServer(server){server.middlewares.use('/__task-notifications',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__task-notifications','<html><body><div id="root" style="height:900px"></div><script type="module" src="/__task-notifications-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
