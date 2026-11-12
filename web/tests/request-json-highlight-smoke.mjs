import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {TracePart} from '/src/components/inspector/RequestTracePanel.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.copy=t
window.reads=[]
api.requestTracePart=async (_session,_trace,part,index,offset)=>{
 window.reads.push({part,offset})
 if(window.fail)return {ok:false,message:'Fixture read failed'}
 const text=offset ? '"tail": false }' : part==='body' ? '{"text":"<img src=x onerror=alert(1)> 原文😀","count":42}' : '{\\n  "text": "<img src=x onerror=alert(1)> 原文😀",\\n  "count": 42,\\n  "flag": true\\n}'
 return {ok:true,data:{text,offset,total_chars:25000,next_offset:offset?null:24000}}
}
const root=createRoot(document.getElementById('root'))
window.show=(part='body',active=true)=>root.render(React.createElement(TracePart,{key:part,sessionId:'fixture',traceId:'trace',part,index:0,label:'Fixture JSON',active}))
window.show()
`
await browserScenario(async ({server,browser}) => {
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.goto(server.resolvedUrls.local[0]+'__request-json')
 const source=page.locator('pre[data-source-language="json"]')
 await source.locator('.hljs-attr').first().waitFor()
 const original=await source.textContent()
 assert.ok(original.includes('<img src=x onerror=alert(1)> 原文😀'))
 assert.equal(await source.locator('img').count(),0)
 for(const locale of ['en','zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
  assert.equal(await source.textContent(),original)
  const colors=await source.evaluate(el=>[getComputedStyle(el).color,getComputedStyle(el.querySelector('.hljs-attr')).color])
  assert.notEqual(colors[0],colors[1])
  await source.focus();assert.equal(await source.evaluate(el=>el===document.activeElement),true)
 }
 assert.equal(await page.evaluate(()=>window.reads.length),1)
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.inspector.RequestTracePanel.4cd4dc1f03d6')),exact:true}).click()
 await page.waitForFunction(()=>document.querySelector('pre')?.textContent==='"tail": false }')
 await source.locator('.hljs-literal').waitFor()
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.components.inspector.RequestTracePanel.5c73eb92d825')),exact:true}).click()
 await page.waitForFunction(()=>document.querySelector('pre')?.textContent?.includes('"count": 42'))
 await page.getByRole('button',{name:/Fixture JSON/}).click()
 await page.waitForFunction(()=>document.querySelector('pre')?.textContent?.startsWith('{"text"'))
 await source.locator('.hljs-number').waitFor()
 for(const part of ['message','tools','tool']){
  await page.evaluate(part=>window.show(part),part)
  await source.locator('.hljs-attr').first().waitFor()
  assert.ok((await source.textContent()).includes('原文😀'))
 }
 await page.evaluate(()=>{window.fail=true;window.show('failure')})
 await page.getByRole('alert').waitFor()
 await page.evaluate(()=>{window.fail=false})
 await page.getByRole('button',{name:await page.evaluate(()=>window.copy('ui.requestComposition.retry')),exact:true}).click()
 await source.locator('.hljs-number').waitFor()
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Request JSON: shared syntax palette, exact text, safe markup, pagination, raw/formatted, bilingual themes, narrow layout and retry passed')
},{entry:'/__request-json-entry.js',vite:{plugins:[{
 name:'request-json-fixture',enforce:'pre',resolveId(id){if(id==='/__request-json-entry.js')return id},
 async load(id){if(id.endsWith('/RequestTracePanel.tsx'))return await readFile(id,'utf8')+'\nexport {TracePart}\n';if(id==='/__request-json-entry.js')return entry},
 configureServer(server){server.middlewares.use('/__request-json',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__request-json','<html><body><div id="root"></div><script type="module" src="/__request-json-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
