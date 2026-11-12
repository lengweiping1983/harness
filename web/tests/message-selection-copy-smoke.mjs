import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React, {useEffect} from 'react'
import {createRoot} from 'react-dom/client'
import {Conversation} from '/src/components/chat/Conversation.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
const root=createRoot(document.getElementById('root'))
useAppStore.setState({activeSessionId:'copy-fixture'})
function Fixture({text,activity}) {
 useEffect(()=>{window.selectionReady=true},[])
 return <><div style={{height:600,display:'flex'}}><Conversation blocks={[
  {id:'user',kind:'user',at:1,text,sourceSeq:1,turn:1},
  activity?{id:'thinking',kind:'thinking',at:2,text:'Thinking outside the selection',turn:1}:
   {id:'reply',kind:'assistant',at:2,text:'Assistant outside the selection'},
 ]} turns={activity?{1:{turn:1,startedAt:1,startSeq:1}}:{}} running={activity} streaming={false} connection="open" /></div><textarea aria-label="Paste receiver" /></>
}
window.configure=async({text,language,dark,activity=false})=>{
 await setLocale(language);document.documentElement.classList.toggle('dark',dark)
 root.render(<Fixture text={text} activity={activity} />)
}
window.configure({text:'开发一款 3D CS 游戏，需要有真实感',language:'en',dark:false})
`
const html='<html><body><div id="root"></div><script type="module" src="/__message-selection-copy-entry.tsx"></script></body></html>'

await browserScenario(async({server,browser})=>{
  const page=await browser.newPage({viewport:{width:1000,height:800}}),errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__message-selection-copy')
  await page.waitForFunction(()=>window.selectionReady===true)
  for(const language of ['en','zh-CN','en'])for(const [dark,width] of [[false,390],[true,1000]])for(const activity of [false,true]) {
    await page.setViewportSize({width,height:800})
    const text='开发一款 3D CS 游戏，需要有真实感'
    await page.evaluate(config=>window.configure(config),{text,language,dark,activity})
    const paragraph=page.locator('[data-user-message-body] p')
    await paragraph.waitFor()
    await page.locator('.conversation-scroll').focus()
    await paragraph.click({clickCount:3})
    const result=await page.evaluate(()=>{
      const selection=window.getSelection(),range=selection.getRangeAt(0)
      const body=document.querySelector('[data-user-message-body]')
      const content=document.createRange();content.selectNodeContents(body)
      const outside=range.cloneRange();outside.setStart(content.endContainer,content.endOffset)
      const fragment=outside.cloneContents()
      const diagnostics={selected:selection.toString(),endInside:body.contains(range.endContainer),
        start:range.startContainer.parentElement?.tagName,end:range.endContainer.parentElement?.tagName,
        outsideText:outside.toString(),outsideElements:Array.from(fragment.querySelectorAll('*')).map(node=>node.tagName)}
      const clipboardData=new DataTransfer()
      const event=new ClipboardEvent('copy',{clipboardData,bubbles:true,cancelable:true})
      // 原生选区配合受控复制事件，覆盖事件落在正文外；不读写系统剪贴板。
      document.querySelector('.conversation-scroll').dispatchEvent(event)
      const text=clipboardData.getData('text/plain')
      const receiver=document.querySelector('textarea')
      receiver.addEventListener('paste',event=>{event.preventDefault();receiver.value=event.clipboardData.getData('text/plain')},{once:true})
      receiver.dispatchEvent(new ClipboardEvent('paste',{clipboardData,bubbles:true,cancelable:true}))
      return {...diagnostics,text,pasted:receiver.value,handled:event.defaultPrevented,html:clipboardData.getData('text/html')}
    })
    assert.equal(result.text,text,JSON.stringify(result))
    assert.equal(result.endInside,false,'Native triple click must exercise the paragraph boundary outside the body')
    assert.equal(result.pasted,text,JSON.stringify(result))
    assert.equal(result.html,'')
    assert.equal(result.handled,true)
  }
  assert.deepEqual(errors,[])
  await page.close()
  console.log('User message copy: native triple clicks, copy events outside the body and controlled paste retain only the selected paragraph across languages, themes and widths')
},{entry:'/__message-selection-copy-entry.tsx',vite:{plugins:[{
  name:'message-selection-copy-fixture',
  resolveId(id){if(id==='/__message-selection-copy-entry.tsx')return id},
  load(id){if(id==='/__message-selection-copy-entry.tsx')return entry},
  configureServer(server){server.middlewares.use('/__message-selection-copy',async(_req,res,next)=>{
    try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__message-selection-copy',html))}catch(error){next(error)}
  })},
}]}})
