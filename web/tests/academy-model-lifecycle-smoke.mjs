import assert from 'node:assert/strict'
import {mkdir,readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AcademyModelLab} from '/src/components/inspector/AcademyModelLab.tsx'
import {setLocale,useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.prompts=[];window.modelRefreshes=0;window.modelStops=[]
const run={id:'model_one',project_id:'project',lesson_id:'lesson',request_id:'request',input_fingerprint:'a'.repeat(64),system:'Frozen system 原始系统指令',prompt:'Frozen input 原文',response:'Original model output / 模型原文 {literal}',model:'actual-model',language:'en',status:'unknown',error_code:'model-timeout',created_at:1}
function App(){const locale=useLocale();const [project,setProject]=useState('project');const [active,setActive]=useState(true);const [error,setError]=useState('model-timeout');const [output,setOutput]=useState(run.response);window.modelOutput=setOutput;const [streaming,setStreaming]=useState(false);window.streaming=setStreaming;const [stopping,setStopping]=useState(false);window.stopping=setStopping;window.project=setProject;window.active=setActive;window.failure=setError;const lesson={id:'lesson',order:1,mode:'model',title:'Lesson',goal:'Goal',reading:'Reading',milestone:false,run_command:[],source_manifest:[],references:[],prerequisites:[],trace_labels:{},model_prompt:locale==='en'?'Default prompt':'默认提示',model_system:locale==='en'?'System instructions':'系统指令'};return React.createElement('main',{'data-project':project,'data-active':active,className:'mx-auto max-w-xl p-4'},active?React.createElement(AcademyModelLab,{key:project,projectId:project,lesson,runs:project==='project'?[streaming?{...run,status:'running',response:'',error_code:undefined,partial_response:'First part / 第一段',stopping}:{...run,response:output,error_code:error,status:error==='model-truncated'?'failed':'unknown'}]:[],disabled:false,asking:false,onAsk:()=>{},onStop:run=>window.modelStops.push(run.id),onRun:prompt=>window.prompts.push(prompt),onRefresh:()=>{window.modelRefreshes++}}):null)}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
const html='<html><body><div id="root"></div><script type="module" src="/__academy-model-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
  for(const theme of ['light','dark'])for(const width of [390,1440]){
    const page=await browser.newPage({viewport:{width,height:1000}}),errors=[]
    page.on('pageerror',error=>errors.push(error.message))
    await page.goto(server.resolvedUrls.local[0]+'__academy-model')
    await page.waitForFunction(()=>typeof window.locale==='function')
    await page.evaluate(theme=>{document.documentElement.className=theme;return window.locale('en')},theme)
    const prompt=page.getByRole('textbox',{name:'Experiment prompt',exact:true})
    await prompt.fill('User prompt 原文 / {literal}')
    for(const language of ['en','zh-CN','en']){
      const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
      await page.evaluate(language=>window.locale(language),language)
      assert.equal(await page.getByRole('textbox',{name:strings['ui.academy.modelPrompt'],exact:true}).inputValue(),'User prompt 原文 / {literal}')
      assert.ok((await page.getByRole('status').textContent()).includes(strings['ui.academy.modelStatus.unknown']))
      await page.getByText(strings['ui.academy.error.model-timeout'],{exact:true}).waitFor()
      assert.equal(await page.getByLabel(strings['ui.academy.modelResponse'],{exact:true}).textContent(),'Original model output / 模型原文 {literal}')
      const options=page.locator('[data-academy-section="model-options"]')
      assert.equal(await options.getAttribute('open'),null)
      assert.equal(await page.getByRole('button',{name:strings['ui.academy.modelReset'],exact:true}).isVisible(),false)
      await options.locator(':scope > summary').click()
      await page.getByText(language==='en'?'System instructions':'系统指令',{exact:true}).waitFor()
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
      await page.getByRole('button',{name:strings['ui.academy.modelRefresh'],exact:true}).click()
      await options.locator(':scope > summary').click()
      assert.equal(await page.getByRole('textbox',{name:strings['ui.academy.modelPrompt'],exact:true}).inputValue(),'User prompt 原文 / {literal}')
      assert.deepEqual(await page.evaluate(()=>window.prompts),[],'Language changes cannot replay a model request')
      const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))
      assert.ok(bounds.scroll<=bounds.width,[theme,width,language].join('/'))
    }
    await page.evaluate(()=>window.streaming(true))
    for(const language of ['en','zh-CN','en']){
      const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
      await page.evaluate(language=>window.locale(language),language)
      const draft=page.getByLabel(strings['ui.academy.modelPartial'],{exact:true})
      await draft.waitFor()
      assert.equal(await draft.getAttribute('aria-busy'),'true')
      assert.ok((await draft.textContent()).includes('First part / 第一段'))
      assert.equal(await page.getByLabel(strings['ui.academy.modelResponse'],{exact:true}).count(),0)
      assert.equal(await page.getByRole('button',{name:strings['ui.academy.modelRun'],exact:true}).count(),0)
      assert.equal(await page.getByRole('button',{name:strings['ui.academy.modelStop'],exact:true}).isEnabled(),true)
      assert.equal(await page.getByRole('textbox',{name:strings['ui.academy.modelPrompt'],exact:true}).inputValue(),'User prompt 原文 / {literal}')
      assert.deepEqual(await page.evaluate(()=>window.prompts),[])
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
    }
    await prompt.dispatchEvent('compositionstart')
    await page.getByRole('button',{name:'Stop',exact:true}).click()
    assert.deepEqual(await page.evaluate(()=>window.modelStops),[])
    await prompt.dispatchEvent('compositionend')
    await page.getByRole('button',{name:'Stop',exact:true}).click()
    assert.deepEqual(await page.evaluate(()=>window.modelStops),['model_one'])
    await page.evaluate(()=>window.stopping(true))
    for(const language of ['en','zh-CN','en']){
      const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
      await page.evaluate(language=>window.locale(language),language)
      assert.equal(await page.getByRole('button',{name:strings['ui.academy.modelStoppingAction'],exact:true}).isDisabled(),true)
      assert.equal(await page.getByLabel(strings['ui.academy.modelPartial'],{exact:true}).count(),0)
      assert.equal(await page.getByRole('status').textContent(),strings['ui.academy.modelStopping'])
    }
    await page.evaluate(()=>window.streaming(false))
    await page.getByLabel('Original model response',{exact:true}).waitFor()
    assert.equal(await page.getByLabel('Answer in progress',{exact:true}).count(),0)
    assert.equal(await page.evaluate(()=>window.modelRefreshes),3)
    const run=page.getByRole('button',{name:'Run prompt',exact:true})
    await page.getByText('Input used for this experiment',{exact:true}).click()
    await page.evaluate(()=>window.modelOutput(Array.from({length:120},(_,index)=>'Original model fragment / 原始片段 '+index).join('\n')))
    for(const language of ['en','zh-CN','en']){
      const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))
      await page.evaluate(language=>{window.failure('model-truncated');return window.locale(language)},language)
      await page.getByText(strings['ui.academy.error.model-truncated'],{exact:true}).waitFor()
      const warning=page.getByText(strings['ui.academy.error.model-truncated'],{exact:true})
      assert.equal(await warning.evaluate((node,label)=>!!(node.compareDocumentPosition(document.querySelector('[aria-label="'+label+'"]'))&Node.DOCUMENT_POSITION_FOLLOWING),strings['ui.academy.modelResponse']),true)
      assert.ok((await page.getByLabel(strings['ui.academy.modelResponse'],{exact:true}).textContent()).includes('原始片段 119'))
      await page.getByText('Frozen system 原始系统指令',{exact:true}).waitFor()
      assert.ok((await page.locator('[data-academy-section="model-evidence"]').innerText()).includes('actual-model'))
      assert.ok((await page.getByRole('status').textContent()).includes(strings['ui.academy.modelStatus.failed']))
      assert.deepEqual(await page.evaluate(()=>window.prompts),[],'Truncated output cannot trigger an automatic continuation')
    }
    for(const keyCode of [13,229]){
      const prevented=await run.evaluate((button,keyCode)=>!button.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode,isComposing:keyCode===13,bubbles:true,cancelable:true})),keyCode)
      assert.equal(prevented,true)
    }
    await prompt.dispatchEvent('compositionstart');await run.click()
    assert.deepEqual(await page.evaluate(()=>window.prompts),[])
    await prompt.dispatchEvent('compositionend')
    await run.click()
    assert.deepEqual(await page.evaluate(()=>window.prompts),['User prompt 原文 / {literal}'])
    await page.evaluate(()=>window.project('other'))
    await page.locator('main[data-project="other"]').waitFor()
    assert.equal(await prompt.inputValue(),'Default prompt')
    await prompt.fill('Other project')
    await page.evaluate(()=>window.project('project'))
    await page.locator('main[data-project="project"]').waitFor()
    assert.equal(await prompt.inputValue(),'User prompt 原文 / {literal}')
    await page.evaluate(()=>window.active(false))
    await page.locator('main[data-active="false"]').waitFor()
    await page.evaluate(()=>window.active(true))
    assert.equal(await prompt.inputValue(),'User prompt 原文 / {literal}')
    if(process.env.HARNESS_TEST_SCREENSHOTS){
      await mkdir(process.env.HARNESS_TEST_SCREENSHOTS,{recursive:true})
      await page.screenshot({path:process.env.HARNESS_TEST_SCREENSHOTS+'/academy-model-'+theme+'-'+width+'.png',fullPage:true})
    }
    await page.locator('[data-academy-section="model-options"] > summary').click()
    await prompt.dispatchEvent('compositionstart')
    await page.getByRole('button',{name:'Restore lesson prompt',exact:true}).click()
    assert.equal(await prompt.inputValue(),'User prompt 原文 / {literal}','Composition must preserve the edited prompt')
    await prompt.dispatchEvent('compositionend')
    await page.getByRole('button',{name:'Restore lesson prompt',exact:true}).click()
    assert.equal(await prompt.inputValue(),'Default prompt')
    await page.evaluate(()=>window.locale('zh-CN'))
    assert.equal(await page.getByRole('textbox',{name:'实验提示词',exact:true}).inputValue(),'默认提示','Unedited defaults follow the current course language')
    const key='harness.academy.model-draft.v1:'+JSON.stringify(['project','lesson'])
    const chinesePrompt=page.getByRole('textbox',{name:'实验提示词',exact:true})
    await chinesePrompt.fill('已确认保存的提示')
    await page.evaluate(key=>{
      const get=Storage.prototype.getItem,set=Storage.prototype.setItem,remove=Storage.prototype.removeItem
      Storage.prototype.getItem=function(name){if(name===key&&window.rejectDraftRead)throw Error('fixture read failure');return get.call(this,name)}
      Storage.prototype.setItem=function(name,value){if(name===key&&window.rejectDraftWrite)throw Error('fixture write failure');return set.call(this,name,value)}
      Storage.prototype.removeItem=function(name){if(name===key&&window.rejectDraftRemove)throw Error('fixture remove failure');return remove.call(this,name)}
      window.restoreDraftStorage=()=>{Storage.prototype.getItem=get;Storage.prototype.setItem=set;Storage.prototype.removeItem=remove}
      window.rejectDraftWrite=true
    },key)
    await chinesePrompt.fill('未保存的新提示 / latest input')
    await page.getByRole('alert').waitFor()
    await page.evaluate(()=>window.active(false))
    await page.locator('main[data-active="false"]').waitFor()
    await page.evaluate(()=>{window.rejectDraftWrite=false;window.active(true)})
    assert.equal(await chinesePrompt.inputValue(),'未保存的新提示 / latest input','A failed write cannot restore older disk text after reopening')
    await page.getByRole('button',{name:'重试保存草稿',exact:true}).click()
    await page.waitForFunction(()=>!document.querySelector('[role="alert"]'))
    assert.equal(await page.evaluate(key=>JSON.parse(sessionStorage.getItem(key)),key),'未保存的新提示 / latest input')
    await page.evaluate(()=>{window.rejectDraftRemove=true})
    await page.locator('[data-academy-section="model-options"] > summary').click()
    await page.getByRole('button',{name:'恢复课程提示',exact:true}).click()
    await page.getByRole('alert').waitFor()
    await page.evaluate(()=>window.active(false))
    await page.locator('main[data-active="false"]').waitFor()
    await page.evaluate(()=>{window.rejectDraftRemove=false;window.active(true)})
    assert.equal(await chinesePrompt.inputValue(),'默认提示','A failed reset preserves its removal intent across reopening')
    await page.getByRole('button',{name:'重试保存草稿',exact:true}).click()
    await page.waitForFunction(()=>!document.querySelector('[role="alert"]'))
    assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),key),null)
    await chinesePrompt.fill('可回退的已保存提示')
    await page.evaluate(()=>{window.rejectDraftRead=true;window.active(false)})
    await page.locator('main[data-active="false"]').waitFor()
    await page.evaluate(()=>window.active(true))
    assert.equal(await chinesePrompt.inputValue(),'可回退的已保存提示','A failed read uses the last confirmed prompt')
    await page.evaluate(()=>{window.rejectDraftRead=false;window.restoreDraftStorage()})
    await page.evaluate(key=>{sessionStorage.setItem(key,'{"broken":true}');window.active(false)},key)
    await page.locator('main[data-active="false"]').waitFor()
    await page.evaluate(()=>window.active(true))
    await page.getByRole('alert').waitFor()
    await page.getByRole('textbox',{name:'实验提示词',exact:true}).fill('内存新草稿')
    assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),key),'{"broken":true}','Corrupt storage is preserved while edits remain on the page')
    assert.equal(await page.getByRole('button',{name:'运行提示',exact:true}).isDisabled(),true)
    assert.deepEqual(errors,[])
    await page.close()
  }
  console.log('Model lab: same-instance en→zh→en, original prompt/output, IME, scoped drafts, reset, hidden/reopened editor and corrupt-storage preservation verified')
},{entry:'/__academy-model-entry.js',vite:{plugins:[{
  name:'academy-model-fixture',resolveId(id){if(id==='/__academy-model-entry.js')return id},load(id){if(id==='/__academy-model-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__academy-model',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__academy-model',html))}catch(error){next(error)}})},
}]}})
