import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {CapabilitiesPanel} from '/src/components/overlays/CapabilitiesPanel.tsx'
import {AutomationsPage} from '/src/components/overlays/AutomationsPage.tsx'
import {RuntimeTab} from '/src/components/inspector/RuntimeTab.tsx'
import {TeamRecipientPicker} from '/src/components/chat/TeamRecipientPicker.tsx'
import {mainConversationBlocks} from '/src/lib/memberDock.ts'
import {makeTeamAddress} from '/src/lib/agentMount.ts'
import {useAppStore} from '/src/store/app.ts'
import {usePermissionStore} from '/src/store/permissions.ts'
import {setLocale, useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
useAppStore.setState({activeSessionId:'fixture',sessions:[{id:'fixture',title:'Original conversation',cwd:'/fixture'}]})
usePermissionStore.setState({sessionId:null,policy:'request_approval'})
const mount = {available:true,agent:'fixture',agent_type:'team',revision:1,members:[{name:'lead',display_name:'Lead',role:'lead',identity:'a'.repeat(64)},{name:'worker',display_name:'Worker',role:'member',identity:'b'.repeat(64)}]}
const address = makeTeamAddress(mount,['lead','worker'])
const team = {id:'team',kind:'team',runId:'run',team:'fixture',at:1,turn:1,phase:'execute',members:[{name:'lead',displayName:'Lead',role:'lead',status:'running'},{name:'worker',displayName:'Worker',role:'member',status:'pending'}],assignments:{worker:'Original task'}}
function Fixture({view}) {
  useLocale()
  return React.createElement('main',null,
    React.createElement('p',{'data-assignment':true},mainConversationBlocks([team])[0].text),
    React.createElement(TeamRecipientPicker,{mount,address,onChange:()=>{}}),
    view === 'capabilities' ? React.createElement(CapabilitiesPanel) : view === 'runtime' ? React.createElement(RuntimeTab) : React.createElement(AutomationsPage))
}
const root = createRoot(document.getElementById('root'))
window.configure = (view,language,theme) => {
  setLocale(language); document.documentElement.className = theme
  root.render(React.createElement(React.StrictMode,null,React.createElement(Fixture,{view})))
}
window.policy = () => usePermissionStore.getState().policy
window.configure('capabilities','en','light')
`
async function finishLateRead(route, data) {
  try {await route.fulfill({json:data})}
  catch (error) {
    assert.match(route.request().failure()?.errorText ?? '', /ERR_ABORTED/)
    assert.match(error.message, /Interception|already handled|aborted/i)
  }
  const response = await route.request().response()
  if (response) await response.finished()
}
const html = '<!doctype html><html><body><div id="root"></div><script type="module" src="/__presentation-entry.js"></script></body></html>'
const settings = () => ({config:{permission_policy:'request_approval',context_auto_manage:true,context_working_budget:10000,summary_max_tokens:null,output_reserve:1000,context_window:12000,tool_result_max_chars:null,tool_schema_mode:'auto',max_steps:null,token_budget:null,network_timeout:60},effective:{model:'Fixture model',context_window:12000,max_tokens:2000,sources:['manual','provider']},tools:[],skills:[]})
await browserScenario(async ({server,browser}) => {
  const errors = []
  for (const language of ['en','zh-CN']) for (const theme of ['light','dark']) for (const width of [390,1440]) {
    const strings = JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
    const page = await browser.newPage({viewport:{width,height:900}})
    page.on('pageerror',error => errors.push(error.message))
    let data = settings(), mode = 'normal', observed
    const held = [], writes = []
    await page.route('**/api/v1/settings', async route => {
      if (route.request().method() === 'PATCH') {
        const patch = route.request().postDataJSON(); writes.push(patch)
        data = {...data,config:{...data.config,...patch}}
        return route.fulfill({json:data})
      }
      if (mode === 'hold') {held.push(route); observed?.(); return}
      if (mode === 'fail') return route.fulfill({status:503,json:{code:'fixture/unavailable',message:'Fixture read unavailable'}})
      return route.fulfill({json:data})
    })
    await page.route('**/api/v1/automations', route => route.fulfill({json:{automations:[],runs:[],scheduler:{running:true,owner:true,last_error:null,max_concurrent:1}}}))
    await page.route('**/api/v1/automations/preview', route => route.fulfill({json:{occurrences:[1800000000]}}))
    await page.route('**/api/v1/runtime?*', route => route.fulfill({json:{sessionId:'fixture',terminals:[],jobs:[],subagents:[
      {agent_id:'parent',description:'Parent task',status:'running',parent_session_id:'fixture',created_at:1,duration_ms:null,tool_calls:0,pending_messages:0},
      {agent_id:'child',description:'Child task',status:'pending',parent_session_id:'fixture',parent_agent_id:'parent',created_at:2,duration_ms:null,tool_calls:0,pending_messages:0},
    ]}}))
    await page.goto(server.resolvedUrls.local[0]+'__presentation')
    await page.getByRole('combobox').last().waitFor()
    await page.evaluate(({language,theme}) => window.configure('capabilities',language,theme),{language,theme})
    assert.equal(await page.locator('dl').count(),0)
    assert.equal(await page.locator('[data-assignment]').textContent(),language === 'en' ? '- Worker (Waiting): Original task' : '- Worker（等待中）：Original task')
    await page.getByText(strings['ui.team.recipients.selected'] + ' · ' + (language === 'en' ? 'Lead, Worker' : 'Lead、Worker'),{exact:true}).waitFor()
    const policy = page.getByLabel(strings['ui.components.overlays.CapabilitiesPanel.d275514a4a9d'],{exact:true})
    await policy.selectOption('full_access')
    await page.waitForFunction(() => window.policy() === 'full_access')
    await policy.waitFor({state:'visible'})
    assert.deepEqual(writes,[{permission_policy:'full_access'}])
    assert.equal(await page.locator('input[type=number]').count(),3)
    const steps = page.getByLabel(strings['ui.components.overlays.CapabilitiesPanel.a4e6d1de3d04'],{exact:true})
    await steps.fill('5'); await steps.press('Tab')
    await page.waitForFunction(() => !document.querySelector('input[type=number]').disabled)
    assert.deepEqual(writes,[{permission_policy:'full_access'},{max_steps:5}])

    await page.evaluate(({language,theme}) => window.configure('automations',language,theme),{language,theme})
    const name = strings['ui.components.overlays.AutomationsPage.582dfbcead41']
    await page.getByRole('button',{name:new RegExp('^'+name)}).click()
    const prompt = page.getByLabel(strings['ui.components.overlays.AutomationsPage.2b7e32bffda6'],{exact:true})
    assert.equal(await prompt.inputValue(),strings['ui.components.overlays.AutomationsPage.2824b8898d76'])
    await page.getByRole('combobox',{name:strings['ui.components.overlays.AutomationsPage.15a1d21d96f9'],exact:true}).selectOption('weekly')
    const days = language === 'en' ? ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'] : ['一','二','三','四','五','六','日']
    for (const day of days) await page.getByRole('button',{name:language === 'en' ? day : '星期'+day,exact:true}).waitFor()
    await prompt.fill('Original edited task')
    const alternate = language === 'en' ? 'zh-CN' : 'en'
    const alternateStrings = JSON.parse(await readFile(new URL('../src/i18n/locales/'+alternate+'.json',import.meta.url)))
    await page.evaluate(({alternate,theme}) => window.configure('automations',alternate,theme),{alternate,theme})
    await page.getByRole('button',{name:alternate === 'en' ? 'Mon' : '星期一',exact:true}).waitFor()
    assert.equal(await page.getByLabel(alternateStrings['ui.components.overlays.AutomationsPage.2b7e32bffda6'],{exact:true}).inputValue(),'Original edited task')

    await page.evaluate(({language,theme}) => window.configure('runtime',language,theme),{language,theme})
    const delegated = strings['ui.runtime.agents.delegatedBy'].replace('{parent}','Parent task').replace('{depth}','2')
    await page.getByText(delegated,{exact:true}).waitFor()
    if (language === 'en') assert.ok(!/[\u4e00-\u9fff、。；：（）」「]/.test(await page.locator('main').textContent()))

    if (language === 'en' && theme === 'light' && width === 390) {
      mode = 'fail'
      await page.evaluate(() => window.configure('capabilities','en','light'))
      await page.getByRole('alert').getByText('Fixture read unavailable',{exact:true}).waitFor()
      mode = 'hold'
      let readObserved
      const started = new Promise(resolve => {readObserved = resolve})
      observed = readObserved
      await page.getByRole('button',{name:strings['ui.requestComposition.retry'],exact:true}).click()
      await started
      mode = 'normal'
      await page.evaluate(() => window.configure('capabilities','zh-CN','light'))
      await page.getByLabel(alternateStrings['ui.components.overlays.CapabilitiesPanel.d275514a4a9d'],{exact:true}).waitFor()
      await page.evaluate(() => window.configure('capabilities','en','light'))
      await page.getByRole('combobox').last().waitFor()
      for (const route of held.splice(0)) await finishLateRead(route,{...data,config:{...data.config,permission_policy:'read_only'},effective:{...data.effective,model:'Stale model'}})
      await page.evaluate(() => new Promise(requestAnimationFrame))
      assert.equal(await page.evaluate(() => window.policy()),'full_access')
      assert.ok(!(await page.locator('main').textContent()).includes('Stale model'))
      mode = 'hold'
      const closingRead = new Promise(resolve => {observed = resolve})
      await page.evaluate(() => window.configure('capabilities','zh-CN','light'))
      await closingRead
      await page.evaluate(() => window.configure('automations','en','light'))
      await page.getByRole('button',{name:/^Daily project briefing/}).waitFor()
      for (const route of held.splice(0)) await finishLateRead(route,{...data,config:{...data.config,permission_policy:'read_only'}})
      await page.evaluate(() => new Promise(requestAnimationFrame))
      assert.equal(await page.evaluate(() => window.policy()),'full_access')
      assert.deepEqual(writes,[{permission_policy:'full_access'},{max_steps:5}],'Read recovery and language changes must not replay the saved setting')
    }
    await page.close()
  }
  assert.deepEqual(errors,[])
  console.log('Presentation language: template prompts, weekday meaning, team grammar, runtime attribution, capability saves/read recovery and stale-language/unmount isolation across eight layouts passed')
},{entry:'/__presentation-entry.js',vite:{plugins:[{
  name:'presentation-fixture',
  resolveId(id) {if (id === '/__presentation-entry.js') return id},
  load(id) {if (id === '/__presentation-entry.js') return entry},
  configureServer(server) {server.middlewares.use('/__presentation',async (_request,response,next) => {
    try {response.setHeader('Content-Type','text/html');response.end(await server.transformIndexHtml('/__presentation',html))}
    catch (error) {next(error)}
  })},
}]}})
