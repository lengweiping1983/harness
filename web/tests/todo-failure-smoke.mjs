import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {browserScenario} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const receipts=JSON.parse(execFileSync(fixturePython,['-c',`
import ast, json
from pathlib import Path
from harness.core.language import current_language
from harness.tools.builtin.todo_state import TodoConflict, TodoSnapshot
codes=set()
for module in ('todo_state.py','todo_tools.py'):
    for node in ast.walk(ast.parse(Path('harness/tools/builtin',module).read_text(encoding='utf-8'))):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='TodoConflict':
            assert isinstance(node.args[0],ast.Constant)
            codes.add(node.args[0].value)
assert codes
snapshot=TodoSnapshot('fixture','list-fixture',source_seq=7,
    todos=[{'id':'original-task','content':'Original 用户 {content}😀','status':'pending'}])
with current_language.set('zh-CN'):
    print(json.dumps([TodoConflict(code,snapshot,invalid_ids=['Original {task} 原文']).payload() for code in sorted(codes)],ensure_ascii=False))
`],{cwd:fileURLToPath(new URL('../../',import.meta.url)),encoding:'utf8',timeout:30000}))

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ToolBlock} from '/src/components/chat/blocks/ToolBlock.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t
const receipts=${JSON.stringify(receipts)}
window.receipts=receipts
createRoot(document.getElementById('root')).render(React.createElement('main',null,...receipts.map(receipt=>React.createElement('section',{'data-code':receipt.code,key:receipt.code},React.createElement(ToolBlock,{defaultOpen:true,block:{kind:'tool',at:0,id:receipt.code,callId:receipt.code,name:'todo_update',status:'error',input:{updates:[]},output:JSON.stringify(receipt),display:receipt}})))))
`
await browserScenario(async({server,browser})=>{
  const page=await browser.newPage(), requests=[], errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{requests.push(route.request().url());return route.fulfill({status:500,json:{error:'Unexpected request'}})})
  await page.goto(server.resolvedUrls.local[0]+'__todo-failure')
  for(const locale of ['en','zh-CN','en']) for(const dark of [false,true]) for(const width of [390,1440]) {
    await page.setViewportSize({width,height:900})
    await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
    for(const receipt of receipts) {
      const code=receipt.code
      const section=page.locator('[data-code="'+code+'"]')
      const copy=await page.evaluate(code=>({reason:window.copy('ui.todoFailure.reason.'+code),unchanged:window.copy('ui.todoFailure.unchanged'),details:window.copy('ui.todoFailure.diagnostics')}),code)
      await section.getByText(copy.reason,{exact:true}).waitFor()
      await section.getByText(copy.unchanged,{exact:true}).waitFor()
      const diagnostics=section.locator('details pre')
      assert.equal(await diagnostics.isVisible(),false)
      const summary=section.getByText(copy.details,{exact:true})
      await summary.focus();await page.keyboard.press('Enter')
      assert.equal(await diagnostics.isVisible(),true)
      assert.deepEqual(JSON.parse(await diagnostics.textContent()),receipt)
      await page.keyboard.press('Enter')
      const fits=await section.evaluate(node=>node.scrollWidth<=node.clientWidth)
      assert.ok(fits,'Failure explanation must fit the viewport')
    }
  }
  assert.deepEqual(await page.evaluate(()=>window.receipts),receipts)
  assert.deepEqual(requests,[]);assert.deepEqual(errors,[])
  console.log('Todo failure: current language, preserved diagnostics, keyboard disclosure, no writes; en→zh→en, themes and widths')
},{entry:'/__todo-failure-entry.js',vite:{plugins:[{name:'todo-failure-fixture',resolveId(id){if(id==='/__todo-failure-entry.js')return id},load(id){if(id==='/__todo-failure-entry.js')return entry},configureServer(server){server.middlewares.use('/__todo-failure',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__todo-failure','<html><body><div id="root"></div><script type="module" src="/__todo-failure-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
