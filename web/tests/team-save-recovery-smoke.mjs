import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AgentDefinitionCreator} from '/src/components/overlays/AgentDefinitionCreator.tsx'
import {teamTemplate} from '/src/lib/teamTemplates.ts'
import {agentDraftKey} from '/src/lib/agentDraft.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import {useAppStore} from '/src/store/app.ts'
import '/src/styles.css'
await setLocale('en');window.locale=setLocale;window.copy=t
const mode=new URLSearchParams(location.search).get('mode'),editing=mode.startsWith('edit')
window.expectedCopy=()=>{
 const reason=['create-empty','create-wrong-name','create-wrong-kind','edit-empty','edit-false','edit-bad-revision','invalid-json'].includes(mode)?t('ui.http.invalidResponse'):mode==='network'?t('ui.lib.api.4a5f658ed9ae',{detail:'Connection lost 原文'}):'Storage result unknown 原文'
 return t(mode==='readback-failed'?'ui.agent.author.saveUnconfirmedReadFailed':'ui.agent.author.saveUnconfirmed',{reason,refreshReason:'Readback failure 原文'})
}
window.writes=[];window.pending=[];window.refreshPending=[];window.refreshes=0;window.toasts=[]
window.draftKey=agentDraftKey(null,editing?'edit:user:team-delivery':'copy:team-delivery')
useAppStore.setState({toast:payload=>window.toasts.push(payload)})
window.fetch=async(path,options={})=>{
 if(options.method&&options.method!=='GET'){
  const payload=JSON.parse(options.body);window.writes.push({path,method:options.method,payload})
  return new Promise((resolve,reject)=>window.pending.push(()=>{
   if(mode==='network')return reject(new TypeError('Connection lost 原文'))
   const status=mode==='rejected'?400:mode==='timeout'?408:['service','readback-failed','replacement','unmount'].includes(mode)?503:200
   const body=mode.endsWith('empty')?{}:mode.endsWith('false')?{updated:false,agent:payload,revision:'b'.repeat(64)}:mode==='create-wrong-name'?{created:true,agent:{...payload,name:'other-team'}}:mode==='create-wrong-kind'?{created:true,agent:{...payload,agent_type:'agent'}}:mode==='edit-bad-revision'?{updated:true,agent:payload,revision:'invalid'}:{error:mode==='rejected'?'Explicit rejection 原文':'Storage result unknown 原文',code:'fixture/failure'}
   resolve(new Response(mode==='invalid-json'?'{':JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}}))
  }))
 }
 return new Response(JSON.stringify(String(path).includes('/personalities')?{profiles:[]}:{runners:[],delegation_enabled:true}),{headers:{'Content-Type':'application/json'}})
}
function App(){
 const [seed,setSeed]=useState({...teamTemplate('delivery'),...(editing?{edit_scope:'user',edit_revision:'a'.repeat(64)}:{})})
 window.replace=()=>setSeed(teamTemplate('product'))
 return React.createElement(AgentDefinitionCreator,{sessionId:null,seed,agents:[],initialKind:'team',refresh:async()=>{
  window.refreshes++;await new Promise((resolve,reject)=>window.refreshPending.push(failed=>failed?reject(new Error('Readback failure 原文')):resolve()))
 }})
}
const root=createRoot(document.getElementById('root'));root.render(React.createElement(App));window.unmount=()=>root.unmount()
`

await browserScenario(async ({server,browser}) => {
  const modes = ['create-empty','create-wrong-name','create-wrong-kind','edit-empty','edit-false','edit-bad-revision','invalid-json','service','network','timeout','readback-failed','rejected','replacement','unmount']
  for (const mode of modes) {
    const page = await browser.newPage(), errors = []
    page.setDefaultTimeout(10000)
    page.on('pageerror', error => errors.push(error.message))
    try {
      await page.goto(server.resolvedUrls.local[0] + '__team-save-recovery?mode=' + mode)
      const label = await page.evaluate(mode => window.copy(mode.startsWith('edit')?'ui.agent.author.editSave':'ui.agent.author.createOnly'), mode)
      await page.getByRole('dialog').getByRole('textbox', {name:await page.evaluate(() => window.copy('ui.agent.author.teamName')),exact:true}).fill('Draft retained 原文')
      await page.waitForFunction(() => sessionStorage.getItem(window.draftKey)?.includes('Draft retained 原文'))
      const original = await page.evaluate(() => sessionStorage.getItem(window.draftKey))
      await page.getByRole('dialog').getByRole('button', {name:label,exact:true}).click()
      await page.waitForFunction(() => window.writes.length === 1)
      await page.evaluate(() => window.pending.shift()())
      await page.waitForFunction(label => window.refreshes === 1 || !document.querySelector('[role="dialog"]') || Array.from(document.querySelectorAll('button')).some(button => button.textContent === label && !button.disabled), label)
      assert.equal(await page.getByRole('dialog').count(), 1, 'Unconfirmed response cannot close or clear the team draft')
      if (mode === 'rejected') {
        assert.equal(await page.evaluate(() => window.refreshes), 0)
        await page.getByRole('alert').filter({hasText:'Explicit rejection 原文'}).waitFor()
      } else {
        assert.equal(await page.evaluate(() => window.refreshes), 1, 'Unknown write must read back before returning control')
        if (mode === 'replacement' || mode === 'unmount') {
          await page.evaluate(mode => mode === 'replacement'?window.replace():window.unmount(), mode)
          await page.evaluate(() => window.refreshPending.shift()())
          if (mode === 'replacement') {
            const create = await page.evaluate(() => window.copy('ui.agent.author.createOnly'))
            await page.waitForFunction(label => Array.from(document.querySelectorAll('button')).some(button => button.textContent === label && !button.disabled), create)
            assert.equal(await page.getByRole('alert').count(), 0)
            assert.equal(await page.getByRole('dialog').getByRole('textbox',{name:await page.evaluate(() => window.copy('ui.agent.author.teamIdentifier')),exact:true,includeHidden:true}).inputValue(),'team-product-custom')
          } else assert.equal(await page.getByRole('dialog').count(), 0)
        } else {
          for (const locale of ['en','zh-CN','en']) for (const dark of [false,true]) for (const width of [390,1440]) {
            await page.setViewportSize({width,height:900})
            await page.evaluate(async ({locale,dark}) => {await window.locale(locale);document.documentElement.classList.toggle('dark',dark)}, {locale,dark})
            const save = page.getByRole('dialog').getByRole('button',{name:await page.evaluate(mode => window.copy(mode.startsWith('edit')?'ui.agent.author.editSave':'ui.agent.author.createOnly'),mode),exact:true})
            assert.ok(await save.isDisabled(), 'Readback retains the synchronous admission lock')
            await save.evaluate(button => {button.click();button.closest('[role="dialog"]').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))})
            assert.equal(await page.getByRole('dialog').count(), 1)
            assert.equal(await page.evaluate(() => window.writes.length), 1)
          }
          await page.evaluate(failed => window.refreshPending.shift()(failed), mode === 'readback-failed')
          await page.waitForFunction(label => Array.from(document.querySelectorAll('button')).some(button => button.textContent === label && !button.disabled), label)
          for (const locale of ['en','zh-CN','en']) {
            await page.evaluate(async locale => window.locale(locale), locale)
            const text = await page.getByRole('alert').last().textContent()
            assert.equal(text, await page.evaluate(() => window.expectedCopy()))
            assert.ok((await page.getByRole('alert').last().getAttribute('class')).includes('text-warn'))
            if (mode === 'readback-failed') assert.ok(text.includes('Readback failure 原文'))
            assert.equal(await page.getByRole('dialog').getByRole('textbox',{name:await page.evaluate(() => window.copy('ui.agent.author.teamName')),exact:true}).inputValue(),'Draft retained 原文')
          }
        }
      }
      assert.equal(await page.evaluate(() => sessionStorage.getItem(window.draftKey)), original)
      assert.equal(await page.evaluate(() => window.writes.length), 1)
      assert.equal(await page.evaluate(() => window.toasts.length), 0)
      assert.deepEqual(errors, [])
      console.log(mode + ': draft preserved, outcome remains explicit, no write replay')
    } finally {
      await page.evaluate(() => {window.pending.forEach(release=>release());window.refreshPending.forEach(release=>release(false))}).catch(() => {})
      await page.close()
    }
  }
}, {entry:'/__team-save-recovery-entry.js',vite:{plugins:[{
  name:'team-save-recovery',
  resolveId(id) {if (id === '/__team-save-recovery-entry.js') return id},
  load(id) {if (id === '/__team-save-recovery-entry.js') return entry},
  configureServer(server) {
    server.middlewares.use('/__team-save-recovery', async (_req,res,next) => {
      try {res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__team-save-recovery','<html><body><div id="root"></div><script type="module" src="/__team-save-recovery-entry.js"></script></body></html>'))}
      catch (error) {next(error)}
    })
  },
}]}})
