import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AcademyTopicStudio} from '/src/components/inspector/AcademyTopicStudio.tsx'
import {academyApi} from '/src/lib/academy.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.posts=0;window.receipts=0;window.reads=0;window.opened=[]
let designed
academyApi.designs=async signal=>{window.reads++;if(window.holdRead)return new Promise(resolve=>{window.readSignal=signal;window.releaseRead=()=>resolve({ok:true,data:{designs:[]}})});return {ok:true,data:{designs:designed?[designed]:[]}}}
academyApi.design=async body=>{window.posts++;if(window.forceUnknown)return {ok:false,status:409,message:'Unconfirmed design',code:'academy/outcome-unknown',copy:{key:'ui.academy.error.outcome-unknown'}};if(window.posts===1)return {ok:false,status:400,message:'',code:'academy/authoring-unavailable',copy:{key:'ui.academy.error.authoring-unavailable'}};designed={id:'design_fixture',request_id:body.request_id,topic:body.topic,background:body.background,course_id:'personal_fixture',language:'en',model:'fixture',created_at:1,status:'ready',phase:'reviewing',review_notes:[]};return new Promise(resolve=>{window.releasePost=()=>resolve({ok:true,data:{request_id:body.request_id,status:'ready',result:{design:designed}}})})}
academyApi.receipt=async id=>{window.receipts++;if(window.forceUnknown)return {ok:true,data:{request_id:id,status:'unknown',result:{}}};return {ok:true,data:{request_id:id,status:'ready',result:{design:designed}}}}
function App(){const [active,setActive]=useState(true);window.active=setActive;return React.createElement('main',{'data-active':active,className:'mx-auto max-w-xl p-4'},React.createElement(AcademyTopicStudio,{active,disabled:false,onCatalogChanged:()=>{},onOpenCourse:id=>window.opened.push(id)}))}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
const html='<html><body><div id="root"></div><script type="module" src="/__academy-topic-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
  for(const theme of ['light','dark'])for(const width of [390,1440]){
    const page=await browser.newPage({viewport:{width,height:1000}}),errors=[]
    page.on('pageerror',error=>errors.push(error.message))
    await page.goto(server.resolvedUrls.local[0]+'__academy-topic')
    await page.waitForFunction(()=>typeof window.locale==='function')
    await page.evaluate(theme=>{document.documentElement.className=theme;window.locale('en')},theme)
    const subject='我想学的主题 / original topic'
    await page.getByRole('textbox',{name:'What would you like to learn?',exact:true}).fill(subject)
    const generate=page.getByRole('button',{name:'Design my course',exact:true})
    for(const keyCode of [13,229]){
      const prevented=await generate.evaluate((button,keyCode)=>!button.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode,isComposing:keyCode===13,bubbles:true,cancelable:true})),keyCode)
      assert.equal(prevented,true)
    }
    await generate.dispatchEvent('compositionstart');await generate.click()
    assert.equal(await page.evaluate(()=>window.posts),0)
    await generate.dispatchEvent('compositionend');await generate.click()
    await page.getByRole('alert').waitFor()
    for(const language of ['en','zh-CN','en']){
      const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
      await page.evaluate(language=>window.locale(language),language)
      const input=page.getByRole('textbox',{name:strings['ui.academy.topic'],exact:true})
      assert.equal(await input.inputValue(),subject)
      assert.equal(await page.getByRole('alert').textContent(),strings['ui.academy.error.authoring-unavailable'])
      assert.equal(await page.evaluate(()=>window.posts),1,'Language changes cannot retry a model-backed write')
      const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))
      assert.ok(bounds.scroll<=bounds.width,[theme,width,language].join('/'))
    }
    // 原挂载实例隐藏后停止观察，迟到列表不能更新隐藏页面。
    await page.evaluate(()=>{window.holdRead=true})
    await page.getByRole('button',{name:'Refresh my course designs',exact:true}).click()
    await page.waitForFunction(()=>typeof window.releaseRead==='function')
    await page.evaluate(()=>window.active(false))
    await page.locator('main[data-active="false"]').waitFor()
    assert.equal(await page.evaluate(()=>window.readSignal.aborted),true)
    const reads=await page.evaluate(()=>window.reads)
    await page.evaluate(()=>{window.releaseRead();window.holdRead=false})
    assert.equal(await page.evaluate(()=>window.reads),reads)
    await page.evaluate(()=>window.active(true))
    await generate.waitFor()
    await generate.evaluate(button=>{button.click();button.click()})
    await page.waitForFunction(()=>typeof window.releasePost==='function')
    await page.evaluate(()=>window.active(false))
    await page.locator('main[data-active="false"]').waitFor()
    await page.evaluate(()=>window.releasePost())
    assert.ok(await page.evaluate(()=>sessionStorage.getItem('harness.academy.design.pending.v1')))
    await page.evaluate(()=>window.active(true))
    await page.getByRole('button',{name:'Start learning',exact:true}).waitFor()
    assert.equal(await page.evaluate(()=>window.posts),2,'Accepted writes cannot be replayed after reopening')
    assert.equal(await page.evaluate(()=>window.receipts),1)
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('harness.academy.design.pending.v1')),null)
    assert.deepEqual(await page.evaluate(()=>window.opened),[],'Ready courses cannot navigate automatically')
    await page.getByRole('button',{name:'Start learning',exact:true}).click()
    assert.deepEqual(await page.evaluate(()=>window.opened),['personal_fixture'])
    await page.getByRole('textbox',{name:'What would you like to learn?',exact:true}).fill('Unconfirmed topic')
    await page.evaluate(()=>{window.forceUnknown=true})
    await generate.click()
    await page.waitForFunction(()=>window.receipts===2)
    assert.ok(await page.evaluate(()=>sessionStorage.getItem('harness.academy.design.pending.v1')),'Domain unknown 409 retains the original request')
    assert.equal(await generate.isDisabled(),true)
    for(const language of ['en','zh-CN','en']){
      await page.evaluate(language=>window.locale(language),language)
      assert.equal(await page.evaluate(()=>window.posts),3,'A domain-unknown write cannot be replayed by language changes')
    }
    assert.deepEqual(errors,[])
    await page.close()
  }
  console.log('Academy topic lifecycle: same-instance en→zh→en, original drafts, localized errors, IME, hidden read cancellation, accepted-write recovery, no replay and explicit course opening verified')
},{entry:'/__academy-topic-entry.js',vite:{plugins:[{
  name:'academy-topic-fixture',resolveId(id){if(id==='/__academy-topic-entry.js')return id},load(id){if(id==='/__academy-topic-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__academy-topic',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__academy-topic',html))}catch(error){next(error)}})},
}]}})
