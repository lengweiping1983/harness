import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const folder = new URL('../../extensions/chatgpt-companion/', import.meta.url)
const adapter = await readFile(new URL('page.mjs', folder), 'utf8')
const entry = `import {inspectPage,sendOnce} from '/__companion_adapter.mjs';
window.expected='https://chatgpt.com/c/00000000-0000-0000-0000-000000000001';window.current=window.expected;
window.composing=false;window.active=false;window.probe=(allowInterrupted=false)=>inspectPage(document,window.expected,window.current,window.composing,window.active,allowInterrupted);
window.send=(prompt,allowInterrupted=false)=>sendOnce(document,window.expected,()=>window.current,prompt,filled=>!window.composing&&!window.active&&(filled||window.probe(allowInterrupted).ready),undefined,allowInterrupted);
document.querySelector('[data-composer-submit]').onclick=event=>{event.preventDefault();const editor=document.querySelector('#mobile-composer-prompt');const node=document.createElement('div');node.dataset.messageAuthorRole='user';node.textContent=editor.value;document.body.append(node);editor.value='';};
window.ready=true;`
const html = `<html><body><div data-message-author-role="assistant" data-message-id="assistant-1">Ready.</div><form data-mobile-composer onsubmit="return false"><textarea id="mobile-composer-prompt"></textarea><input type="file" multiple><button type="submit" data-composer-submit><span data-composer-send-icon>Send</span><span data-composer-loading-icon hidden>Loading</span><span data-composer-stop-icon hidden>Stop</span></button></form><script type="module" src="/__companion_entry.js"></script></body></html>`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage()
 const errors=[];page.on('pageerror', error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__companion');await page.waitForFunction(()=>window.ready)
 assert.equal((await page.evaluate(()=>window.probe())).ready,true)
 await page.locator('#mobile-composer-prompt').fill('my draft')
 assert.equal((await page.evaluate(()=>window.probe())).reason,'draft')
 const guarded=await page.evaluate(()=>window.send('automatic'))
 assert.equal(guarded.outcome,'not_sent');assert.equal(await page.locator('#mobile-composer-prompt').inputValue(),'my draft')
 await page.locator('#mobile-composer-prompt').fill('')
 for(const flag of ['composing','active']){
  await page.evaluate(flag=>window[flag]=true,flag)
  assert.equal((await page.evaluate(()=>window.send('automatic'))).outcome,'not_sent')
  await page.evaluate(flag=>window[flag]=false,flag)
 }
 await page.evaluate(()=>{const node=document.createElement('div');node.dataset.testid='attachment-preview';document.querySelector('form').append(node)})
 assert.equal((await page.evaluate(()=>window.probe())).ready,false)
 await page.locator('[data-testid="attachment-preview"]').evaluate(node=>node.remove())
 await page.evaluate(()=>window.current='https://chatgpt.com/c/00000000-0000-0000-0000-000000000002')
 assert.equal((await page.evaluate(()=>window.send('automatic'))).outcome,'not_sent')
 await page.evaluate(()=>window.current=window.expected)
 const sent=await page.evaluate(()=>window.send('[Harness fixture-unique] continue'))
 assert.equal(sent.outcome,'confirmed')
 assert.equal(await page.locator('[data-message-author-role="user"]').count(),1)
 const imageKeys=[]
 for(const color of ['cyan','magenta']){
  const result=await page.evaluate(async color=>{
   const canvas=document.createElement('canvas');canvas.width=12;canvas.height=12
   canvas.getContext('2d').fillStyle=color;canvas.getContext('2d').fillRect(0,0,12,12)
   const reply=document.createElement('div');reply.dataset.messageAuthorRole='assistant'
   const image=document.createElement('img');image.src=canvas.toDataURL();reply.append(image);document.body.append(reply)
   await image.decode()
   return window.probe()
  },color)
  assert.equal(result.hasImage,true)
  assert.match(result.assistantKey,/^[0-9a-f]{16}$/)
  imageKeys.push(result.assistantKey)
 }
 assert.notEqual(imageKeys[0],imageKeys[1])
 await page.evaluate(()=>{const reply=document.createElement('div');reply.dataset.messageAuthorRole='assistant';reply.append(document.createElement('img'));document.body.append(reply)})
 assert.equal((await page.evaluate(()=>window.probe())).hasImage,false)
 await page.locator('[data-composer-submit]').evaluate(node=>node.remove())
 const unsupported=await page.evaluate(()=>window.send('do not duplicate'))
 assert.equal(unsupported.outcome,'unknown')
 assert.equal(await page.locator('[data-message-author-role="user"]').count(),1)
 assert.equal(await page.locator('#mobile-composer-prompt').inputValue(),'do not duplicate')
 await page.evaluate(()=>{document.body.innerHTML='<div data-message-author-role="assistant">Container.</div><div data-composer-wrap-inner><textarea aria-label="Chat"></textarea><button type="submit" data-composer-submit><span data-composer-send-icon>Send</span></button></div>';window.current=window.expected})
 assert.equal((await page.evaluate(()=>window.probe())).ready,true)
 await page.evaluate(()=>{document.body.innerHTML='<div data-message-author-role="assistant">Desktop.</div><form><textarea name="prompt-textarea"></textarea><button type="submit" id="composer-submit-button">Send</button></form>';window.current=window.expected})
 assert.equal((await page.evaluate(()=>window.probe())).ready,true)
 await page.evaluate(()=>{document.body.innerHTML='<div data-message-author-role="assistant">Generic.</div><form><textarea></textarea><button type="submit">Send</button></form>';window.current=window.expected})
 assert.equal((await page.evaluate(()=>window.probe())).ready,true)
 await page.evaluate(()=>{document.body.innerHTML='<div data-message-author-role="assistant">ChatGPT composer.</div><form data-chatgpt-composer><div contenteditable="true" role="textbox" aria-label="询问 ChatGPT"></div><button type="submit" aria-label="发送" hidden>Send</button></form>';window.current=window.expected})
 assert.equal((await page.evaluate(()=>window.probe())).ready,true)
 await page.evaluate(()=>{document.body.innerHTML='<div data-message-author-role="assistant">Legacy.</div><form><textarea id="prompt-textarea"></textarea><button type="button" data-testid="send-button">Send</button></form>';window.current=window.expected})
 assert.equal((await page.evaluate(()=>window.probe())).ready,true)
 // 真实登录态：无旧消息标记、角色位于标题、纯图片回复、隐藏 composer、动态发送按钮。
 await page.evaluate(async()=>{
  document.body.innerHTML=`<div data-turn-key="account-turn"><div data-talvt-turn-state="complete"><div>
   <div><h4 data-conversation-role="user">You said</h4><p>User request</p></div>
   <div><h4 data-conversation-role="assistant">ChatGPT said</h4><div data-testid="generated-image-gallery"><button type="button" data-testid="generated-image-preview"><img></button></div></div>
  </div></div></div><form data-chatgpt-composer hidden><div contenteditable="true" role="textbox">Hidden draft</div></form>
  <form data-chatgpt-composer onsubmit="return false"><div contenteditable="true" role="textbox" data-composer-markdown></div><button type="button" aria-label="开始语音">Voice</button></form>`
  const canvas=document.createElement('canvas');canvas.width=12;canvas.height=12
  canvas.getContext('2d').fillRect(0,0,12,12)
  const img=document.querySelector('img');img.src=canvas.toDataURL();await img.decode()
  const editor=document.querySelector('form:not([hidden]) [role="textbox"]')
  editor.addEventListener('input',()=>{
   if(!editor.textContent.trim()||editor.parentElement.querySelector('[type="submit"]'))return
   const button=document.createElement('button');button.type='submit';button.ariaLabel='发送'
   button.onclick=event=>{
    event.preventDefault()
    const turn=document.createElement('div');turn.dataset.turnKey='account-receipt'
    const body=document.createElement('div');const heading=document.createElement('h4');heading.dataset.conversationRole='user';heading.textContent='You said'
    const text=document.createElement('p');text.textContent=editor.textContent;body.append(heading,text);turn.append(body);document.body.prepend(turn)
    editor.textContent='';button.remove()
   }
   editor.parentElement.append(button)
  })
 })
 const modern=await page.evaluate(()=>window.probe())
 assert.equal(modern.ready,true);assert.equal(modern.hasAssistant,true);assert.equal(modern.hasImage,true)
 assert.deepEqual(modern.userMessages,['You saidUser request'])
 assert.equal(await page.locator('form:not([hidden]) button[type="submit"]').count(),0)
 await page.locator('[data-talvt-turn-state]').evaluate(node=>node.setAttribute('data-talvt-turn-state','running'))
 assert.equal((await page.evaluate(()=>window.probe())).reason,'busy')
 await page.locator('[data-talvt-turn-state]').evaluate(node=>node.setAttribute('data-talvt-turn-state','complete'))
 assert.equal((await page.evaluate(()=>window.send('[Harness account-fixture] continue'))).outcome,'confirmed')
 assert.equal(await page.locator('h4[data-conversation-role="user"]').count(),2)
 assert.equal(await page.locator('form[hidden] [role="textbox"]').textContent(),'Hidden draft')
 await page.locator('[data-talvt-turn-state]').evaluate(node=>node.setAttribute('data-talvt-turn-state','cancelled'))
 assert.equal((await page.evaluate(()=>window.probe())).ready,false)
 assert.equal((await page.evaluate(()=>window.probe(true))).ready,true)
 assert.equal((await page.evaluate(()=>window.send('继续',true))).outcome,'confirmed')
 assert.equal(await page.locator('h4[data-conversation-role="user"]').count(),3)
 await page.locator('[data-talvt-turn-state]').evaluate(node=>node.setAttribute('data-talvt-turn-state','complete'))
 await page.evaluate(()=>{
  const editor=document.querySelector('form:not([hidden]) [role="textbox"]')
  editor.addEventListener('input',()=>{const attachment=document.createElement('div');attachment.dataset.testid='attachment-preview';editor.parentElement.append(attachment)},{once:true})
 })
 assert.equal((await page.evaluate(()=>window.send('[Harness protected-fixture] continue'))).outcome,'unknown')
 assert.equal(await page.locator('h4[data-conversation-role="user"]').count(),3)
 await page.evaluate(()=>{
  document.body.innerHTML='<main><article data-turn-key="previous"><div data-talvt-turn-state="complete"><div data-message-author-role="user">继续</div><div data-message-author-role="assistant">Earlier reply</div></div></article><form data-chatgpt-composer onsubmit="return false"><textarea id="prompt-textarea"></textarea><button type="submit" data-composer-submit>Send</button></form></main>'
  const form=document.querySelector('form'),editor=document.querySelector('textarea')
  form.querySelector('button').onclick=event=>{
   event.preventDefault()
   const turn=document.createElement('article');turn.dataset.turnKey='stream-error-turn'
   turn.innerHTML='<div data-talvt-turn-state="error"><h4 data-conversation-role="user">You said</h4><div class="group/user-message" data-chatgpt-search-message-ids="new-user-message"><div>'+editor.value+'</div><span data-markdown-copy="exclude">17:55</span></div><aside role="alert" class="bg-background-danger-surface/20"><span>消息流中的错误</span><button type="button">重试</button></aside></div>'
   form.before(turn);editor.value=''
  }
 })
 assert.equal((await page.evaluate(()=>window.send('继续',true))).outcome,'confirmed')
 assert.equal((await page.evaluate(()=>window.probe(true))).ready,true)
 await page.locator('aside button').evaluate(node=>{node.disabled=true})
 assert.equal((await page.evaluate(()=>window.probe(true))).ready,false)
 assert.deepEqual(errors,[])
 console.log('Chat companion: real DOM send receipt, drafts, attachments, IME, user input, wrong chat and changed controls passed')
},{vite:{plugins:[{name:'chat-companion-fixture',resolveId(id){if(['/__companion_adapter.mjs','/__companion_entry.js'].includes(id))return id},load(id){if(id==='/__companion_adapter.mjs')return adapter;if(id==='/__companion_entry.js')return entry},configureServer(server){server.middlewares.use('/__companion',async(_req,res)=>{res.setHeader('Content-Type','text/html');res.end(html)})}}]}})
