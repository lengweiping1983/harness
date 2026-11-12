import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry = `
import React, {useRef} from 'react'
import {createRoot} from 'react-dom/client'
import {useComposerEditor} from '/src/hooks/useComposerEditor.ts'
import {useComposerSubmission} from '/src/hooks/useComposerSubmission.ts'
import {ComposerAttachments} from '/src/components/chat/ComposerAttachments.tsx'
import '/src/styles.css'
import {useComposerKeyboard} from '/src/hooks/useComposerKeyboard.ts'
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
window.files=0;window.calls=0;window.clears=0;window.cancels=0;window.menu=false;window.shortcuts=false
useAppStore.setState({activeSessionId:null})
useDraftStore.getState().setText('__unsaved__','hello')
function Fixture(){
 const pickerRef=useRef(null)
 const drafts=useDraftStore(s=>s.bySession.__unsaved__?.attachments??[])
 const value=useDraftStore(s=>s.bySession.__unsaved__?.text??'')
 const {captureFrames}=useComposerEditor(value,'__unsaved__')
 const {submit,submitting}=useComposerSubmission({captureFrames,value,draftKey:'__unsaved__',sessionId:null,
 mode:'normal',hasGoal:false,planMode:false,disconnected:false,canSend:Boolean(value),controlBusy:false,
 attachments:{readyRefs:[],clearSent:()=>window.clears++},onSend:()=>{window.calls++;return new Promise(resolve=>window.resolveSend=resolve)},
 selectMode:mode=>window.mode=mode,setValue:text=>useDraftStore.getState().setText('__unsaved__',text),setTrigger:()=>{},resize:()=>{}})
 const key=useComposerKeyboard({running:true,shortcutsOpen:window.shortcuts,setShortcutsOpen:open=>window.shortcuts=open,
 onCancel:()=>window.cancels++,submit,handleCompletionKey:()=>window.menu})
 window.submit=submit;window.newDraft=()=>{useAppStore.setState(s=>({newSessionRevision:s.newSessionRevision+1}));useDraftStore.getState().setText('__unsaved__','new draft')}
 return React.createElement('div',null,React.createElement('textarea',{value,readOnly:true,onKeyDown:key,'data-submitting':String(submitting)}),React.createElement(ComposerAttachments,{pickerRef,attachments:{drafts,remove:id=>useDraftStore.getState().updateAttachments('__unsaved__',rows=>rows.filter(row=>row.localId!==id)),addFiles:files=>{window.files+=files.length}}}))
}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
const html='<html><body><div id="root"></div><script type="module" src="/__submission-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage()
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__submission')
 await page.waitForFunction(()=>Boolean(window.submit))
 await page.evaluate(()=>{void window.submit();void window.submit()})
 assert.equal(await page.evaluate(()=>window.calls),1)
 await page.evaluate(()=>{window.newDraft();window.resolveSend(true)})
 await page.waitForFunction(()=>document.querySelector('textarea').dataset.submitting==='false')
 assert.equal(await page.locator('textarea').inputValue(),'new draft')
 assert.equal(await page.evaluate(()=>window.clears),0)
 await page.evaluate(()=>{window.menu=true})
 await page.locator('textarea').press('Enter')
 assert.equal(await page.evaluate(()=>window.calls),1)
 await page.evaluate(()=>{window.menu=false})
 await page.locator('textarea').press('Escape')
 assert.equal(await page.evaluate(()=>window.cancels),1)
 await page.locator('textarea').dispatchEvent('keydown',{key:'Enter',isComposing:true})
 assert.equal(await page.evaluate(()=>window.calls),1)
 await page.evaluate(()=>{void window.submit()})
 await page.evaluate(()=>window.resolveSend(false))
 await page.waitForFunction(()=>document.querySelector('textarea').dataset.submitting==='false')
 assert.equal(await page.locator('textarea').inputValue(),'new draft')
 await page.evaluate(()=>{void window.submit()})
 await page.evaluate(()=>window.resolveSend(true))
 await page.waitForFunction(()=>document.querySelector('textarea').value==='')
 assert.equal(await page.evaluate(()=>window.clears),1)
 await page.evaluate(async()=>{const {useDraftStore}=await import('/src/store/drafts.ts');useDraftStore.getState().restoreQueuedText('__unsaved__','',[{attachmentId:'a'.repeat(64),name:'Original.txt',mediaType:'text/plain',size:5,isImage:false}])})
 await page.locator('input[type=file]').setInputFiles({name:'fixture.txt',mimeType:'text/plain',buffer:Buffer.from('fixture')})
 assert.equal(await page.evaluate(()=>window.files),1)
 assert.equal(await page.locator('input[type=file]').inputValue(),'')
 await page.getByRole('button',{name:await page.evaluate(async()=>{const {t}=await import('/src/i18n/index.ts');return t('ui.components.chat.AttachmentDraft.715b458f46f0',{p0:'Original.txt'})}),exact:true}).click()
 assert.equal(await page.evaluate(async()=>{const {useDraftStore}=await import('/src/store/drafts.ts');return useDraftStore.getState().bySession.__unsaved__.attachments.length}), 0)
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Composer submission: duplicate admission, stale revision, rejection, accepted cleanup, menu priority, IME and Escape passed')
},{entry:'/__submission-entry.js',vite:{plugins:[{name:'submission-fixture',resolveId(id){if(id==='/__submission-entry.js')return id},load(id){if(id==='/__submission-entry.js')return entry},configureServer(server){server.middlewares.use('/__submission',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__submission',html))}catch(error){next(error)}})}}]}})
