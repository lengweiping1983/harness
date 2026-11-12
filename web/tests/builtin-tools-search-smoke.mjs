import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entryPath='/__builtin-search-entry.js'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {BuiltinToolsPage} from '/src/components/overlays/BuiltinToolsPage.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.fetch=async()=>Response.json({message:'fixture settings unavailable'},{status:503})
window.copy=t;window.language=setLocale
window.populate=(empty=false)=>useAppStore.setState({tools:empty?[]:[
 {name:'read',description:'Read Original 文件',category:'file',category_label:'Files 原文',dangerous:false},
 {name:'write',description:'Write Original 文件',category:'file',category_label:'Files 原文',dangerous:true},
 {name:'remote_'+ 'long'.repeat(70),description:'https://example.test/'+ 'long'.repeat(150),category:'mcp',category_label:'MixedCase 分类',dangerous:false}
]})
setLocale('en');window.populate()
createRoot(document.getElementById('root')).render(React.createElement(BuiltinToolsPage))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__builtin-search')
 for(const width of [390,1440])for(const dark of [false,true])for(const language of ['en','zh-CN','en']){
  await page.setViewportSize({width,height:1000})
  await page.evaluate(async({dark,language})=>{await window.language(language);document.documentElement.classList.toggle('dark',dark)},{dark,language})
  const copy=key=>page.evaluate(key=>window.copy(key),key)
  const catalog=page.getByRole('region',{name:await copy('ui.settings.tools.title'),exact:true})
  const search=catalog.getByRole('searchbox')
  await search.waitFor()
  assert.ok(await search.evaluate(el=>el.clientWidth)>200)
  await search.fill('Files 原文')
  assert.equal(await catalog.locator('summary').count(),2)
  assert.equal(await catalog.getByRole('status').innerText(),language==='en'?'2 matches out of 3':'匹配 2 项，共 3 项')
  await search.fill('read original')
  assert.equal(await catalog.locator('summary').count(),1)
  await catalog.locator('summary').focus();await page.keyboard.press('Enter')
  assert.equal(await catalog.locator('details').getAttribute('open'),'')
  await catalog.getByText('Read Original 文件',{exact:true}).waitFor()
  await search.fill('missing tool')
  assert.equal(await catalog.locator('summary').count(),0)
  await catalog.getByText(await copy('ui.settings.tools.noMatchHint'),{exact:true}).waitFor()
  await catalog.getByRole('button',{name:await copy('ui.settings.tools.clear'),exact:true}).click()
  assert.equal(await search.inputValue(),'')
  assert.equal(await search.evaluate(el=>el===document.activeElement),true)
  assert.equal(await catalog.locator('summary').count(),3)
  assert.equal(await catalog.locator('h4').last().evaluate(el=>getComputedStyle(el).textTransform),'none')
  await catalog.locator('summary').last().click()
  assert.equal(await catalog.evaluate(el=>el.scrollWidth<=el.clientWidth),true)
  if(language==='zh-CN'&&!dark&&width===1440&&process.env.HARNESS_TEST_TOOLS_SCREENSHOT)await catalog.screenshot({path:process.env.HARNESS_TEST_TOOLS_SCREENSHOT})
  await page.evaluate(()=>window.populate(true))
  await catalog.getByText(await copy('ui.settings.tools.empty'),{exact:true}).waitFor()
  assert.equal(await catalog.getByRole('status').count(),0)
  await page.evaluate(()=>window.populate())
 }
 assert.deepEqual(errors,[])
 console.log('Built-in tools: name/description/category search, counts, clear/focus, keyboard disclosure, empty states, bilingual themes and widths passed')
},{entry:entryPath,vite:{plugins:[{name:'builtin-search-fixture',resolveId(id){if(id===entryPath)return id},load(id){if(id===entryPath)return entry},configureServer(server){server.middlewares.use('/__builtin-search',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__builtin-search','<html><body><div id="root"></div><script type="module" src="'+entryPath+'"></script></body></html>'))}catch(error){next(error)}})}}]}})
