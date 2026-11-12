import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AgentDefinitionCreator} from '/src/components/overlays/AgentDefinitionCreator.tsx'
import {teamTemplate} from '/src/lib/teamTemplates.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale;window.copy=t
const mode=new URLSearchParams(location.search).get('mode')
window.writes=[];window.pending=[];window.refreshes=0;window.refreshPending=[]
window.fetch=async(path,options={})=>{
 if(options.method&&options.method!=='GET'){
  const payload=JSON.parse(options.body);window.writes.push({path,payload})
  return new Promise(resolve=>window.pending.push((rejected=false)=>resolve(new Response(JSON.stringify(rejected?{error:'Old team rejection 原文',code:'agent/invalid'}:String(path).endsWith('/preview')?{agent:teamTemplate('delivery')}:{created:true,agent:payload}),{status:rejected?400:200,headers:{'Content-Type':'application/json'}}))))
 }
 return new Response(JSON.stringify(String(path).includes('/personalities')?{profiles:[]}:{runners:[],delegation_enabled:true}),{headers:{'Content-Type':'application/json'}})
}
function App(){
 const [seed,setSeed]=useState(mode.startsWith('import')?null:teamTemplate('delivery'))
 window.replace=()=>setSeed(teamTemplate('product'))
 return React.createElement(AgentDefinitionCreator,{sessionId:null,seed,agents:[],initialKind:'team',refresh:async()=>{
  window.refreshes++
  if(mode.startsWith('refresh')&&window.refreshes===1)await new Promise((resolve,reject)=>window.refreshPending.push(rejected=>rejected?reject(new Error('Old refresh rejection 原文')):resolve()))
 }})
}
createRoot(document.getElementById('root')).render(React.createElement(App))
`

await browserScenario(async ({server,browser}) => {
  for (const mode of ['save-success','save-rejected','import-success','import-rejected','refresh-success','refresh-rejected']) {
    const page = await browser.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    try {
      await page.goto(server.resolvedUrls.local[0] + '__team-editor-lifecycle?mode=' + mode)
      if (mode.startsWith('import')) {
        await page.locator('input[type=file]').evaluate(input => {
          const transfer = new DataTransfer()
          transfer.items.add(new File(['Fixture 原文'], 'AGENTS.md', {type:'text/markdown'}))
          input.files = transfer.files
          input.dispatchEvent(new Event('change', {bubbles:true}))
        })
      } else {
        const label = await page.evaluate(() => window.copy('ui.agent.author.createOnly'))
        await page.getByRole('dialog').getByRole('button', {name:label, exact:true}).click()
      }
      await page.waitForFunction(() => window.writes.length === 1)
      const refreshing = mode.startsWith('refresh')
      if (refreshing) {
        await page.evaluate(() => window.pending.shift()())
        await page.waitForFunction(() => window.refreshes === 1)
      }
      await page.evaluate(() => window.replace())
      const identifier = page.getByRole('dialog').getByRole('textbox', {name:await page.evaluate(() => window.copy('ui.agent.author.teamIdentifier')), exact:true, includeHidden:true})
      await page.waitForFunction(() => Array.from(document.querySelectorAll('input')).some(input => input.value === 'team-product-custom'))
      for (const locale of ['en','zh-CN','en']) for (const dark of [false,true]) for (const width of [390,1440]) {
        await page.setViewportSize({width,height:900})
        await page.evaluate(async ({locale,dark}) => {await window.locale(locale);document.documentElement.classList.toggle('dark',dark)}, {locale,dark})
        assert.equal(await page.getByRole('dialog').count(), 1)
        assert.equal(await page.evaluate(() => window.writes.length), 1)
      }
      await page.evaluate(({rejected,refreshing}) => (refreshing?window.refreshPending:window.pending).shift()(rejected), {rejected:mode.endsWith('rejected'),refreshing})
      const create = page.getByRole('dialog').getByRole('button', {name:await page.evaluate(() => window.copy('ui.agent.author.createOnly')), exact:true})
      await page.waitForFunction(label => !document.querySelector('[role="dialog"]') || Array.from(document.querySelectorAll('button')).some(button => button.textContent === label && !button.disabled), await page.evaluate(() => window.copy('ui.agent.author.createOnly')))
      assert.equal(await page.getByRole('dialog').count(), 1, 'Old save cannot close the replacement team draft')
      assert.equal(await identifier.inputValue(), 'team-product-custom', 'Old response cannot replace or close the new team draft')
      assert.equal(await page.getByText('Old team rejection 原文', {exact:false}).count(), 0, 'Old rejection belongs to its original draft')
      assert.equal(await page.getByText('Old refresh rejection 原文', {exact:false}).count(), 0, 'Old refresh rejection cannot enter the replacement editor')
      assert.equal(await page.evaluate(() => window.refreshes), refreshing?1:0, 'Obsolete operation cannot call the replacement editor refresh')
      assert.equal(await page.evaluate(() => window.writes.length), 1)
      await page.getByText(await page.evaluate(() => window.copy('ui.agent.author.advanced')), {exact:true}).click()
      await identifier.fill('product-replacement-' + mode)
      await create.click()
      await page.waitForFunction(() => window.writes.length === 2)
      const write = await page.evaluate(() => window.writes[1])
      assert.equal(write.path, '/api/v1/plugins/agents/create')
      assert.equal(write.payload.name, 'product-replacement-' + mode)
      assert.equal(write.payload.members[1].name, 'discovery')
      await page.evaluate(() => window.pending.shift()())
      await page.waitForFunction(expected => window.refreshes === expected, refreshing?2:1)
      await page.getByRole('dialog').waitFor({state:'detached'})
      assert.deepEqual(errors, [])
      console.log(mode + ': replacement draft retained; explicit new save completed once')
    } finally {
      await page.evaluate(() => {window.pending.forEach(release => release(true));window.refreshPending.forEach(release => release(false))}).catch(() => {})
      await page.close()
    }
  }
}, {entry:'/__team-editor-lifecycle-entry.js',vite:{plugins:[{
  name:'team-editor-lifecycle',
  resolveId(id) {if (id === '/__team-editor-lifecycle-entry.js') return id},
  load(id) {if (id === '/__team-editor-lifecycle-entry.js') return entry},
  configureServer(server) {
    server.middlewares.use('/__team-editor-lifecycle', async (_req,res,next) => {
      try {res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__team-editor-lifecycle','<html><body><div id="root"></div><script type="module" src="/__team-editor-lifecycle-entry.js"></script></body></html>'))}
      catch (error) {next(error)}
    })
  },
}]}})
