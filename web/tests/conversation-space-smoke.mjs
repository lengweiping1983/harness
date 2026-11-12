import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {ConversationPane} from '/src/components/chat/ConversationPane.tsx'
import {Composer} from '/src/components/chat/Composer.tsx'
import {useDraftStore} from '/src/store/drafts.ts'
import {useAppStore} from '/src/store/app.ts'
import {readGeneration} from '/src/lib/generation.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const root=createRoot(document.getElementById('root'))
const text=['Reading paragraph 原文 '.repeat(80),'\\n\\n~~~js\\nconst original = "Full workspace width";\\n~~~','\\n\\n| Original | Result |\\n| --- | --- |\\n| Kept | Visible |'].join('')
const blocks=[{id:'reply',kind:'assistant',at:1,text}]
const todos=[{id:'one',content:'Original checklist task 原文',status:'in_progress'},{id:'two',content:'Original completed task 原文',status:'completed'}]
const generation=readGeneration({request_id:'fixture',phase:'waiting',model:'original/actual-request-model',elapsed_ms:6000})
window.copy=t
window.configure=({language='en',dark=false,size=14,paneWidth=null,phase='waiting',tasks=true,active=true,short=false,withHistory=false}={})=>{
 setLocale(language);document.documentElement.classList.toggle('dark',dark);document.documentElement.style.fontSize=size+'px'
 useAppStore.setState({activeSessionId:null})
 const history=withHistory?{through:2,start:0,end:2,total:2,has_more:false,next_cursor:null,user_messages:[],cursorThrough:2,generation:1,revision:0,loading:false,error:null,resetRequired:false,ids:['request','reply']}:null
 root.render(<div style={{height:'100dvh',display:'flex',width:paneWidth??'100%'}}><ConversationPane title="Original conversation" conversation={{blocks:short?[{id:'request',kind:'user',at:1,text:'Original user message 原文'},{...blocks[0],text:'Short reply 原文'}]:blocks,running:active,streaming:false,connection:'open',history}}>
  <Composer running={active} queued={0} planMode={false} connection="open" generation={active?{...generation,phase}:null} todos={tasks?todos:[]} onSend={()=>{window.sent=true}} onCancel={()=>{window.stopped=true}} onTogglePlan={()=>{}}/>
 </ConversationPane></div>)
}
window.draft=text=>useDraftStore.getState().setText('__unsaved__',text)
window.configure()
`

await browserScenario(async ({server,browser}) => {
  const page=await browser.newPage({viewport:{width:1600,height:1000}}), errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>route.fulfill({status:503,json:{message:'Fixture unavailable'}}))
  await page.goto(server.resolvedUrls.local[0]+'__conversation-space')
  const editor=page.locator('.composer-editor')
  const generation=page.locator('.composer-generation'), tasks=page.locator('.composer-tasks')
  for(const dark of [false,true]) for(const width of [390,1024,1600,2240]) for(const language of ['en','zh-CN','en']) {
    await page.setViewportSize({width,height:1000})
    await page.evaluate(config=>window.configure(config),{language,dark})
    await editor.waitFor()
    await page.locator('.conversation-scroll pre').waitFor()
    const geometry=await page.evaluate(()=>{
      const rect=selector=>document.querySelector(selector).getBoundingClientRect().toJSON()
      return {card:rect('.composer-card'),editor:rect('.composer-editor'),rail:rect('.composer-status-rail'),
        generation:rect('.composer-generation'),tasks:rect('.composer-tasks'),code:rect('.conversation-scroll pre'),
        paragraph:rect('.conversation-scroll .md p'),dock:rect('.composer-dock'),scroll:document.documentElement.scrollWidth}
    })
    assert.ok(geometry.scroll<=width,'No document overflow')
    assert.ok(geometry.card.width>Math.min(width-80,1200),'Available workspace width is used')
    assert.ok(1000-geometry.dock.bottom<=10,'Composer and project settings stay near the bottom')
    assert.ok(geometry.editor.height<=70,'Empty editor leaves room for the conversation')
    assert.ok(Math.abs(geometry.tasks.top-geometry.generation.top)<2,'Normal size status and checklist share one row')
    assert.ok(geometry.rail.height<=34,'Collapsed status rail occupies one compact row')
    assert.ok(Math.abs(geometry.code.right-geometry.paragraph.right)<2,'Paragraphs and code share the same right edge instead of wrapping at a narrower limit')
    await generation.locator('summary').focus()
    await page.keyboard.press('Enter')
    await generation.locator('dd').getByText('original/actual-request-model',{exact:true}).waitFor()
    assert.ok((await generation.boundingBox()).width>geometry.card.width-2,'Expanded details use the full lane')
    await page.evaluate(()=>window.configure({language:'zh-CN'}))
    assert.equal(await generation.getAttribute('open'),'','Language changes keep the disclosure open')
    await generation.locator('summary').press('Enter')
    await tasks.locator('summary').click()
    await tasks.locator('li').getByText('Original checklist task 原文',{exact:true}).waitFor()
    await tasks.locator('summary').press('Enter')
  }
  for(const paneWidth of [320,640,960]) for(const size of [10,22]) {
    await page.evaluate(config=>window.configure(config),{paneWidth,size})
    await editor.waitFor()
    assert.ok(await editor.evaluate(node=>node.getBoundingClientRect().right<=node.closest('main').getBoundingClientRect().right),'Container sizing follows the middle pane')
    await page.evaluate(()=>window.draft('Original long draft 原文\n'.repeat(80)))
    await page.waitForFunction(()=>{const node=document.querySelector('.composer-editor');return node.scrollHeight>node.clientHeight})
    assert.equal(await editor.evaluate(node=>getComputedStyle(node).overflowY),'auto','Long drafts remain scrollable')
    await page.evaluate(()=>window.draft(''))
  }
  await page.evaluate(()=>window.configure({phase:'retrying'}))
  await generation.getByText(await page.evaluate(()=>window.copy('ui.lib.generation.2575219eec28')),{exact:true}).waitFor()
  await page.evaluate(()=>window.configure({active:false,tasks:false}))
  await generation.waitFor({state:'detached'});await tasks.waitFor({state:'detached'})
  assert.equal(await page.locator('.composer-status-rail').evaluate(node=>node.getBoundingClientRect().height),0,'Empty rail reserves no height')
  for(const dark of [false,true]) for(const language of ['en','zh-CN','en']) for(const width of [390,1600]) for(const withHistory of [false,true]) {
    await page.setViewportSize({width,height:1000})
    await page.evaluate(config=>window.configure(config),{language,dark,short:true,active:false,tasks:false,withHistory})
    await page.getByText('Original user message 原文',{exact:true}).waitFor()
    await page.getByText('Short reply 原文',{exact:true}).waitFor()
    assert.equal(await page.locator('[data-row-id="conversation-history"]').count(),0,'Complete history does not show an event count or reserve a history row')
    for(const selectParagraph of [false,true]) {
      const copied=await page.locator('[data-user-message-body]').evaluate((body,selectParagraph)=>{
        const selection=window.getSelection(),range=document.createRange()
        if(selectParagraph)range.selectNode(body.querySelector('p'))
        else range.selectNodeContents(body)
        selection.removeAllRanges();selection.addRange(range)
        const clipboardData=new DataTransfer()
        clipboardData.setData('text/html','<p>Original user message 原文</p>')
        const event=new ClipboardEvent('copy',{clipboardData,bubbles:true,cancelable:true})
        body.dispatchEvent(event)
        selection.removeAllRanges()
        return {text:clipboardData.getData('text/plain'),html:clipboardData.getData('text/html'),handled:event.defaultPrevented}
      },selectParagraph)
      assert.deepEqual(copied,{text:'Original user message 原文',html:'',handled:true},'User selection copies only its visible text without paragraph markup or added blank lines')
    }
    await page.waitForFunction(()=>{
      const scroll=document.querySelector('.conversation-scroll'),first=scroll.querySelector('[data-row-id]')
      return first&&Math.abs(first.getBoundingClientRect().top-scroll.getBoundingClientRect().top)<3
    })
    const shortGeometry=await page.evaluate(()=>{
      const scroll=document.querySelector('.conversation-scroll'),user=scroll.querySelector('[data-row-id="request"]')
      return {userOffset:user.getBoundingClientRect().top-scroll.getBoundingClientRect().top,
        scrollTop:scroll.scrollTop,overflow:scroll.scrollHeight-scroll.clientHeight,
        composerBottom:document.querySelector('.composer-dock').getBoundingClientRect().bottom}
    })
    assert.ok(shortGeometry.userOffset>=0&&shortGeometry.userOffset<160,'Short user messages follow the history/date headings at the top')
    assert.equal(shortGeometry.scrollTop,0,'Short conversations do not scroll away from the user message')
    assert.ok(shortGeometry.overflow<=1,'Short conversations leave remaining space below the messages')
    assert.ok(1000-shortGeometry.composerBottom<=10,'Top-aligned messages keep the composer near the bottom')
  }
  if(process.env.HARNESS_TEST_SCREENSHOTS) {
    await mkdir(process.env.HARNESS_TEST_SCREENSHOTS,{recursive:true})
    await page.setViewportSize({width:1600,height:1000})
    await page.evaluate(()=>window.configure({language:'zh-CN',dark:true}))
    await page.locator('.conversation-scroll pre').waitFor()
    await page.waitForFunction(()=>{
      const scroll=document.querySelector('.conversation-scroll'),first=scroll.querySelector('[data-row-id]'),last=scroll.querySelector('[data-row-id]:last-child')
      return scroll.scrollHeight<=scroll.clientHeight+1
        ? first&&Math.abs(first.getBoundingClientRect().top-scroll.getBoundingClientRect().top)<3
        : last&&Math.abs(scroll.getBoundingClientRect().bottom-last.getBoundingClientRect().bottom)<3
    })
    await page.screenshot({path:process.env.HARNESS_TEST_SCREENSHOTS+'/conversation-space.png'})
  }
  assert.deepEqual(errors,[])
  await page.close()
  console.log('Conversation space: top-aligned short messages with history/date headings, responsive lanes, unified paragraph/code widths, compact status rail, native keyboard disclosures, locale retention and long drafts passed')
},{entry:'/__conversation-space-entry.tsx',vite:{plugins:[{
  name:'conversation-space-fixture',
  resolveId(id){if(id==='/__conversation-space-entry.tsx')return id},
  load(id){if(id==='/__conversation-space-entry.tsx')return entry},
  configureServer(server){server.middlewares.use('/__conversation-space',async(_req,res,next)=>{
    try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__conversation-space','<html><body><div id="root"></div><script type="module" src="/__conversation-space-entry.tsx"></script></body></html>'))}catch(error){next(error)}
  })},
}]}})
