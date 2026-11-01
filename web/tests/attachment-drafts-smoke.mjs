import assert from 'node:assert/strict'
import {createHash} from 'node:crypto'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React, {useRef} from 'react'
import {createRoot} from 'react-dom/client'
import {useAttachmentDrafts} from '/src/hooks/useAttachmentDrafts.ts'
import {useComposerEditor} from '/src/hooks/useComposerEditor.ts'
import {useComposerSubmission} from '/src/hooks/useComposerSubmission.ts'
import {ComposerAttachments} from '/src/components/chat/ComposerAttachments.tsx'
import {useAppStore} from '/src/store/app.ts'
import {useDraftStore} from '/src/store/drafts.ts'
import {noticeText} from '/src/lib/noticeText.ts'
import {setLocale, useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.feedback=noticeText
window.reset = (language, theme) => {
  setLocale(language); document.documentElement.className = theme
  useAppStore.setState({activeSessionId:'a', sessions:[{id:'a',cwd:'/fixture/a'},{id:'b',cwd:'/fixture/b'}], toasts:[]})
  useDraftStore.setState({bySession:{}, submissions:{}})
  useDraftStore.getState().setText('a', 'original message')
  window.sent = []; window.resolveSend = null
}
window.reset('en', 'light')
window.snapshot = () => ({bySession:useDraftStore.getState().bySession, toasts:useAppStore.getState().toasts, sent:window.sent})
window.seedBudget = () => useDraftStore.getState().updateAttachments('a', () => [32,32,31].map((size,index) => ({localId:'seed-'+index,name:'seed-'+index+'.txt',mediaType:'text/plain',size:size*1024*1024,isImage:false,attachmentId:'a'.repeat(64)})))
window.changeDraft = () => {
  useDraftStore.getState().setEditOrigin('a',{sessionId:'a',sourceSeq:41})
  useDraftStore.getState().setText('a', 'new typed draft')
}
window.startDelayed = () => {
  const file = new File(['delayed text'], 'delayed.txt')
  file.slice = () => ({arrayBuffer:() => new Promise(resolve => {window.resolveHead = () => resolve(new TextEncoder().encode('delayed text').buffer)})})
  window.delayed = window.addFiles([file])
}
window.select = id => useAppStore.getState().selectSession(id)
window.discardA = () => {
  useAppStore.setState(state => ({sessions:state.sessions.filter(session => session.id !== 'a')}))
  useDraftStore.getState().discard('a')
}
function Fixture() {
  useLocale()
  const attachments = useAttachmentDrafts()
  const sessionId = useAppStore(state => state.activeSessionId)
  const value = useDraftStore(state => state.bySession[sessionId]?.text ?? '')
  const pickerRef = useRef(null)
  const {captureFrames}=useComposerEditor(value,sessionId)
  const {submit, submitting} = useComposerSubmission({captureFrames,value, draftKey:sessionId, sessionId,
    mode:'normal', hasGoal:false, planMode:false, disconnected:false, canSend:attachments.ready, controlBusy:false,
    attachments, onSend:(text, refs) => {window.sent.push({text, refs}); return new Promise(resolve => {window.resolveSend = resolve})},
    selectMode:() => {}, setValue:text => useDraftStore.getState().setText(sessionId,text), setTrigger:() => {}, resize:() => {}})
  window.addFiles = attachments.addFiles; window.submit = submit
  return React.createElement('div', null,
    React.createElement('textarea', {value, readOnly:true, 'data-submitting':String(submitting), 'data-session':sessionId}),
    React.createElement(ComposerAttachments, {attachments, pickerRef}))
}
createRoot(document.getElementById('root')).render(React.createElement(React.StrictMode,null,React.createElement(Fixture)))
`
const html = '<!doctype html><html><body><div id="root"></div><script type="module" src="/__attachments-entry.js"></script></body></html>'
await browserScenario(async ({server, browser}) => {
  const errors = []
  for (const language of ['en','zh-CN']) for (const theme of ['light','dark']) for (const width of [390,1440]) {
    const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
    const page = await browser.newPage({viewport:{width,height:900}})
    page.on('pageerror', error => errors.push(error.message))
    let uploads = 0, unicodeUploads = 0
    await page.route('**/api/v1/attachments', async route => {
      uploads += 1
      const files = route.request().postDataJSON().files
      if (files[0].name === 'failed.txt') return route.fulfill({status:500,json:{code:'fixture/failure',message:'Fixture upload failure'}})
      const attachments = files.map(file => {
        const bytes = Buffer.from(file.data,'base64')
        if (file.name === '原文') {assert.equal(bytes.toString(), '中'.repeat(1400)); unicodeUploads += 1}
        return {attachmentId:createHash('sha256').update(bytes).digest('hex'),name:file.name,mediaType:'text/plain',size:bytes.length,isImage:false}
      })
      await route.fulfill({json:{attachments,stats:{files:uploads,bytes:0}}})
    })
    await page.goto(server.resolvedUrls.local[0] + '__attachments')
    await page.waitForFunction(() => Boolean(window.addFiles))
    await page.evaluate(({language,theme}) => window.reset(language,theme), {language,theme})
    await page.evaluate(async () => {
      const files = prefix => Array.from({length:11}, (_,index) => new File(['fixture'], prefix+index+'.txt'))
      await Promise.all([window.addFiles(files('first-')),window.addFiles(files('second-'))])
    })
    await page.waitForFunction(() => window.snapshot().bySession.a?.attachments.length === 20 && window.snapshot().bySession.a.attachments.every(item => item.attachmentId))
    assert.equal(uploads,20,'Concurrent batches must share the twenty-file draft budget')
    assert.equal(await page.getByRole('listitem').count(),20)

    await page.evaluate(({language,theme}) => {window.reset(language,theme); window.seedBudget()}, {language,theme})
    const beforeBudget = uploads
    await page.evaluate(async () => {
      const bytes = new Uint8Array(1024*1024).fill(97)
      await Promise.all([window.addFiles([new File([bytes],'budget-one.txt')]),window.addFiles([new File([bytes],'budget-two.txt')])])
    })
    await page.waitForFunction(() => window.snapshot().bySession.a?.attachments.length === 4 && window.snapshot().bySession.a.attachments.every(item => item.attachmentId))
    assert.equal(uploads-beforeBudget,1,'Concurrent batches must share the remaining byte budget')
    assert.equal(await page.evaluate(() => window.snapshot().bySession.a.attachments.reduce((total,item) => total+item.size,0)),96*1024*1024)

    await page.evaluate(({language,theme}) => window.reset(language,theme), {language,theme})
    await page.evaluate(async () => window.addFiles([new File(['same bytes'],'good.txt'),new File(['failure'],'failed.txt')]))
    await page.waitForFunction(() => window.snapshot().bySession.a?.attachments.some(item => item.status === 'error') && window.snapshot().bySession.a.attachments.some(item => item.attachmentId))
    await page.evaluate(() => {void window.submit()})
    await page.waitForFunction(() => Boolean(window.resolveSend))
    assert.equal(await page.evaluate(() => window.snapshot().sent[0].refs.length),1)
    assert.equal(await page.evaluate(() => window.snapshot().sent[0].refs[0].name),'good.txt')
    await page.evaluate(async () => {window.changeDraft(); await window.addFiles([new File(['same bytes'],'fresh.txt')])})
    await page.waitForFunction(() => window.snapshot().bySession.a.attachments.find(item => item.name === 'fresh.txt')?.attachmentId)
    await page.evaluate(() => window.resolveSend(true))
    await page.waitForFunction(() => document.querySelector('textarea').dataset.submitting === 'false' && window.snapshot().bySession.a.attachments.length === 2)
    assert.deepEqual(await page.evaluate(() => window.snapshot().bySession.a.attachments.map(item => item.name)),['failed.txt','fresh.txt'])
    assert.equal(await page.locator('textarea').inputValue(),'new typed draft')
    assert.equal(await page.evaluate(() => window.snapshot().bySession.a.editOrigin.sourceSeq),41)
    assert.equal(await page.getByRole('listitem').count(),2)
    assert.equal(await page.evaluate(() => window.snapshot().sent.length),1)

    await page.evaluate(({language,theme}) => window.reset(language,theme), {language,theme})
    await page.evaluate(() => {void window.submit()})
    await page.waitForFunction(() => Boolean(window.resolveSend))
    await page.evaluate(async () => {
      const {useDraftStore} = await import('/src/store/drafts.ts')
      useDraftStore.getState().setEditOrigin('a',{sessionId:'a',sourceSeq:42})
      window.resolveSend(true)
    })
    await page.waitForFunction(() => document.querySelector('textarea').dataset.submitting === 'false')
    assert.equal(await page.locator('textarea').inputValue(),'original message','A newly selected edit with identical text must survive an older send receipt')
    assert.equal(await page.evaluate(() => window.snapshot().bySession.a.editOrigin.sourceSeq),42)

    await page.evaluate(({language,theme}) => window.reset(language,theme), {language,theme})
    await page.evaluate(async () => window.addFiles([new File(['中'.repeat(1400)],'原文')]))
    await page.waitForFunction(() => window.snapshot().bySession.a?.attachments[0]?.attachmentId)
    assert.equal(unicodeUploads,1,'An extensionless UTF-8 file must pass the short browser probe unchanged')
    const beforeReadFailure = uploads
    await page.evaluate(async () => {
      const file = new File(['unreadable'],'unreadable.txt')
      file.slice = () => ({arrayBuffer:async () => {throw Error('private original diagnostic')}})
      await window.addFiles([file])
    })
    assert.equal(uploads,beforeReadFailure)
    assert.equal(await page.evaluate(() => window.feedback(window.snapshot().toasts.at(-1).description)),translations['ui.attachments.fileRejected'].replace('{name}','unreadable.txt').replace('{error}',translations['ui.attachments.readFailed']))
    await page.evaluate(async () => window.addFiles([new File(['denied'],'one.exe'),new File(['denied'],'two.sh')]))
    const rejection = await page.evaluate(() => window.feedback(window.snapshot().toasts.at(-1).description))
    assert.ok(rejection.includes(language === 'en' ? 'one.exe: ' : 'one.exe：'))
    assert.ok(rejection.includes(language === 'en' ? '; ' : '；'))
    if (language === 'en') assert.ok(!/[：；\u4e00-\u9fff]/.test(rejection))

    await page.evaluate(({language,theme}) => window.reset(language,theme), {language,theme})
    await page.waitForFunction(() => document.querySelector('textarea').dataset.session === 'a')
    await page.evaluate(() => window.startDelayed())
    await page.waitForFunction(() => Boolean(window.resolveHead))
    await page.evaluate(async () => {window.select('b'); window.resolveHead(); await window.delayed})
    await page.waitForFunction(() => window.snapshot().bySession.a?.attachments[0]?.attachmentId)
    assert.equal(await page.evaluate(() => window.snapshot().bySession.b?.attachments?.length ?? 0),0)

    await page.evaluate(({language,theme}) => {window.reset(language,theme); window.resolveHead = null}, {language,theme})
    await page.waitForFunction(() => document.querySelector('textarea').dataset.session === 'a')
    await page.evaluate(() => window.startDelayed())
    await page.waitForFunction(() => Boolean(window.resolveHead))
    const beforeDelete = uploads
    await page.evaluate(async () => {window.select('b'); window.discardA(); window.resolveHead(); await window.delayed})
    assert.equal(await page.evaluate(() => window.snapshot().bySession.a),undefined)
    assert.equal(uploads,beforeDelete,'Deleting the destination must prevent delayed admission and upload')
    await page.close()
  }
  assert.deepEqual(errors,[])
  console.log('Attachment drafts: atomic file/byte budgets, failed and later attachment preservation, draft edits, Unicode probes, localized rejection and destination isolation across eight layouts passed')
}, {entry:'/__attachments-entry.js',vite:{plugins:[{
  name:'attachment-fixture',
  resolveId(id) {if (id === '/__attachments-entry.js') return id},
  load(id) {if (id === '/__attachments-entry.js') return entry},
  configureServer(server) {
    server.middlewares.use('/__attachments',async (_request,response,next) => {
      try {response.setHeader('Content-Type','text/html'); response.end(await server.transformIndexHtml('/__attachments',html))}
      catch (error) {next(error)}
    })
  },
}]}})
