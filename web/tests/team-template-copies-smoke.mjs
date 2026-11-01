import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {TeamTemplates} from '/src/components/overlays/TeamTemplates.tsx'
import {AgentDefinitionCreator} from '/src/components/overlays/AgentDefinitionCreator.tsx'
import {teamTemplate} from '/src/lib/teamTemplates.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale;window.copy=t
window.catalog=[
 {...teamTemplate('delivery'),name:'team-delivery-custom',system_prompt:'Existing delivery 原文'},
 {...teamTemplate('delivery'),name:'team-delivery-2-custom'},
 {...teamTemplate('security'),name:'team-security-custom'},
 {...teamTemplate('performance'),name:'team-performance-custom',agent_type:'agent'},
 {...teamTemplate('research'),name:'team-research'},
]
window.original=JSON.stringify(window.catalog);window.choices=[];window.refreshed=0
function App(){
 const [seed,setSeed]=useState(null),[agents,setAgents]=useState(window.catalog.slice())
 return React.createElement(React.Fragment,null,
  React.createElement(TeamTemplates,{agents,onChoose:team=>{window.choices.push(team);setSeed(team)}}),
  React.createElement(AgentDefinitionCreator,{sessionId:null,seed,agents,initialKind:'team',refresh:async()=>{window.refreshed++;setAgents(window.catalog.slice())}}))
}
createRoot(document.getElementById('root')).render(React.createElement(App))
`

await browserScenario(async ({server,browser}) => {
  const page = await browser.newPage()
  const errors = [], writes = []
  page.on('pageerror', error => errors.push(error.message))
  let rejectNext = false
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (request.method() !== 'GET') {
      assert.equal(request.method(), 'POST')
      assert.equal(path, '/api/v1/plugins/agents/create')
      const body = request.postDataJSON()
      writes.push(body)
      const occupied = await page.evaluate(name => window.catalog.some(agent => agent.name === name), body.name)
      if (rejectNext || occupied) return route.fulfill({status:409,json:{code:'agent/exists',error:'Fixture concurrent conflict 原文'}})
      await page.evaluate(agent => window.catalog.push(agent), body)
      return route.fulfill({json:{created:true,agent:body}})
    }
    return route.fulfill({json:path.endsWith('/personalities')?{profiles:[]}:{runners:[],delegation_enabled:true}})
  })
  try {
    await page.goto(server.resolvedUrls.local[0] + '__team-template-copies')
    const cases = [
      ['delivery','team-delivery-3-custom','en'],
      ['delivery','team-delivery-4-custom','zh-CN'],
      ['security','team-security-2-custom','en'],
      ['product','team-product-custom','zh-CN'],
      ['performance','team-performance-2-custom','en'],
      ['research','team-research-custom','en'],
    ]
    let completed = 0
    for (const [id,name,locale] of cases) {
      await page.evaluate(async locale => window.locale(locale), locale)
      const label = await page.evaluate(id => window.copy('ui.teamTemplates.chooseNamed',{name:window.copy('ui.teamTemplates.'+id+'.name')}), id)
      await page.getByRole('button', {name:label,exact:true}).click()
      const identifier = page.getByRole('dialog').getByRole('textbox', {name:await page.evaluate(() => window.copy('ui.agent.author.teamIdentifier')),exact:true,includeHidden:true})
      assert.equal(await identifier.inputValue(), name, 'Fresh template copy must avoid the current catalog namespace')
      await page.getByRole('dialog').getByRole('button', {name:await page.evaluate(() => window.copy('ui.agent.author.createOnly')),exact:true}).click()
      await page.waitForFunction(expected => window.refreshed === expected, ++completed)
      await page.getByRole('dialog').waitFor({state:'detached'})
      assert.equal(writes.at(-1).name, name)
      assert.equal(writes.at(-1).agent_type, 'team')
    }
    assert.equal(writes.length, cases.length)
    assert.equal(await page.evaluate(() => JSON.stringify(window.catalog.slice(0,5)) === window.original), true, 'Existing definitions remain byte-for-byte unchanged')
    // 目录读取后的竞争仍由已有同名拒绝处理，不自动改名并重放写入。
    rejectNext = true
    await page.getByRole('button', {name:await page.evaluate(() => window.copy('ui.teamTemplates.chooseNamed',{name:window.copy('ui.teamTemplates.security.name')})),exact:true}).click()
    await page.getByRole('dialog').getByRole('button', {name:await page.evaluate(() => window.copy('ui.agent.author.createOnly')),exact:true}).click()
    await page.getByRole('alert').filter({hasText:'Fixture concurrent conflict 原文'}).waitFor()
    for (const locale of ['en','zh-CN','en']) for (const dark of [false,true]) for (const width of [390,1440]) {
      await page.setViewportSize({width,height:900})
      await page.evaluate(async ({locale,dark}) => {await window.locale(locale);document.documentElement.classList.toggle('dark',dark)}, {locale,dark})
      assert.equal(await page.getByRole('alert').filter({hasText:'Fixture concurrent conflict 原文'}).count(), 1)
      assert.equal(writes.length, cases.length + 1)
      assert.equal(await page.getByRole('dialog').getByRole('textbox',{name:await page.evaluate(() => window.copy('ui.agent.author.teamIdentifier')),exact:true,includeHidden:true}).inputValue(),'team-security-3-custom')
    }
    assert.equal(await page.evaluate(() => window.refreshed), cases.length)
    assert.deepEqual(errors, [])
    console.log('Five team templates: catalog-aware copies, refreshed names, original definitions retained and concurrent conflict not replayed')
  } finally {await page.close()}
}, {entry:'/__team-template-copies-entry.js',vite:{plugins:[{
  name:'team-template-copies',
  resolveId(id) {if (id === '/__team-template-copies-entry.js') return id},
  load(id) {if (id === '/__team-template-copies-entry.js') return entry},
  configureServer(server) {
    server.middlewares.use('/__team-template-copies', async (_req,res,next) => {
      try {res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__team-template-copies','<html><body><div id="root"></div><script type="module" src="/__team-template-copies-entry.js"></script></body></html>'))}
      catch (error) {next(error)}
    })
  },
}]}})
