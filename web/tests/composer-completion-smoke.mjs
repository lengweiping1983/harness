import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React, {useCallback,useState} from 'react'
import {createRoot} from 'react-dom/client'
import {useComposerCompletion} from '/src/hooks/useComposerCompletion.ts'
import {useComposerEditor} from '/src/hooks/useComposerEditor.ts'
import {TriggerMenu} from '/src/components/chat/TriggerMenu.tsx'
import {detectTrigger} from '/src/lib/trigger.ts'
import {useAppStore} from '/src/store/app.ts'
import {BrowserActivityBar} from '/src/components/inspector/BrowserActivityBar.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
useAppStore.setState({activeSessionId:'a',sessions:[{id:'a',resolved_cwd:'/fixture/a'},{id:'b',resolved_cwd:'/fixture/b'}],skills:[]})
window.sends = 0
function Fixture() {
  const [draft,setDraft] = useState('a')
  const [texts,setTexts] = useState({a:'',b:''})
  const value = texts[draft]
  const setValue = useCallback(text=>setTexts(current=>({...current,[draft]:text})),[draft])
  const editor = useComposerEditor(value,draft)
  const completion = useComposerCompletion({...editor,draftKey:draft,sessionId:draft,setValue,selectMode:mode=>{window.mode=mode}})
  window.switchDraft = id=>{useAppStore.setState({activeSessionId:id});setDraft(id)}
  return React.createElement('div',{ref:editor.dockRef},
    React.createElement('div',{ref:editor.wrapRef},React.createElement('textarea',{
      'aria-label':'fixture draft',ref:editor.taRef,value,onClick:completion.syncTrigger,onChange:event=>{const text=event.target.value;const next=detectTrigger(text,event.target.selectionStart);setValue(text);completion.setTrigger(next?{...next,owner:draft}:null)},
      onKeyDown:event=>{if(event.nativeEvent.isComposing||event.nativeEvent.keyCode===229)return;if(completion.handleCompletionKey(event))return;if(event.key==='Enter'){event.preventDefault();window.sends++}}
    })),
    completion.trigger?React.createElement(TriggerMenu,{id:'fixture-completions',anchor:editor.wrapRef,items:completion.items,activeIndex:completion.activeIndex,title:'Fixture completions',onPick:completion.pick,onClose:()=>completion.setTrigger(null)}):null,
    React.createElement(BrowserActivityBar,{sessionId:'a',pageId:'tab',active:true}))
}
setLocale('en')
window.locale = setLocale
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
window.ready = true
`
const html = '<!doctype html><html><body><div id="root"></div><script type="module" src="/__completion-entry.js"></script></body></html>'
await browserScenario(async ({server,browser})=>{
  const page=await browser.newPage({viewport:{width:390,height:900}})
  const errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  let releaseSlow, held=false
  const slow=new Promise(resolve=>{releaseSlow=resolve})
  await page.route('**/api/v1/sessions/*/files/search?*',async route=>{
    const url=new URL(route.request().url()),id=url.pathname.split('/')[4],query=url.searchParams.get('q')
    if(query==='slow'&&!held){held=true;await slow}
    const name=query==='quote'?'quote file.txt':query+'.txt'
    await route.fulfill({json:{session_id:id,query,root:'/fixture/'+id,source:'filesystem',has_more:false,truncated:false,matches:[{kind:'file',path:name,absolute_path:'/fixture/'+id+'/'+name}]}})
  })
  await page.route('**/api/v1/sessions/a/browser',route=>route.fulfill({json:{started:true,owner:'agent',control_revision:0,active_tab:'tab',tabs:[],viewport:{width:1280,height:800},activity:null,history:[],health:{errors:2,warnings:3,total:5,dropped:0}}}))
  await page.goto(server.resolvedUrls.local[0]+'__completion')
  const input=page.getByRole('textbox',{name:'fixture draft'})
  await input.waitFor()
  await page.getByRole('button',{name:'Page diagnostics 2 errors, 3 warnings',exact:true}).waitFor()
  await input.fill('@quote')
  await page.getByRole('option',{name:/^quote file\.txt/}).waitFor()
  assert.match(await page.getByRole('listbox').textContent(),/^File1/)
  await input.press('Enter')
  assert.equal(await input.inputValue(),'"/fixture/a/quote file.txt" ')
  assert.equal(await page.evaluate(()=>window.sends),0,'Accepting a completion must not send a message')
  assert.equal(await input.evaluate(element=>element.selectionStart), (await input.inputValue()).length)
  await input.fill('/clear')
  await page.getByRole('option',{name:'/clear',exact:true}).waitFor()
  await input.press('Tab')
  assert.equal(await input.inputValue(),'')
  assert.equal(await page.evaluate(()=>window.sends),0)
  await input.fill('@slow')
  await page.waitForRequest(request=>new URL(request.url()).searchParams.get('q')==='slow')
  await page.evaluate(()=>window.switchDraft('b'))
  await page.waitForFunction(()=>document.querySelector('textarea').value==='')
  await input.fill('@fast')
  await page.getByRole('option',{name:/^fast\.txt/}).waitFor()
  releaseSlow()
  await input.press('Enter')
  assert.equal(await input.inputValue(),'/fixture/b/fast.txt ')
  await page.setViewportSize({width:1440,height:900})
  await page.evaluate(()=>{document.documentElement.classList.add('dark');window.locale('zh-CN')})
  await page.getByRole('button',{name:'页面诊断 错误 2，警告 3',exact:true}).waitFor()
  await page.evaluate(()=>window.switchDraft('a'))
  await page.waitForFunction(()=>document.querySelector('textarea').value==='@slow')
  await input.click()
  await page.getByRole('option',{name:/^slow\.txt/}).waitFor()
  assert.match(await page.getByRole('listbox').textContent(),/^文件1/)
  await input.press('Escape')
  assert.equal(await page.getByRole('listbox').count(),0)
  assert.equal(await input.inputValue(),'@slow')
  assert.deepEqual(errors,[])
  await page.close()
  console.log('Composer hooks: quoted file completion, committed caret, menu Enter/Tab isolation, slash dispatch, draft switching, obsolete file response and bilingual diagnostic counts passed')
},{entry:'/__completion-entry.js',vite:{plugins:[{name:'completion-fixture',resolveId(id){if(id==='/__completion-entry.js')return id},load(id){if(id==='/__completion-entry.js')return entry},configureServer(server){server.middlewares.use('/__completion',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__completion',html))}catch(error){next(error)}})}}]}})
