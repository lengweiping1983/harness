import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const source='Name,Value\r\n"Alpha, one",10\r\nBeta,2\r\n"<img src=x onerror=alert(1)>",3\r\n'+Array.from({length:120},(_,i)=>'Group '+i+','+(i+4)+'\r\n').join('')
const chart={xAxis:{data:['A','B']},yAxis:{},series:[{type:'bar',data:[3,8]}]}
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Markdown} from '/src/components/markdown/MarkdownContent.tsx'
import {ToolBlock} from '/src/components/chat/blocks/ToolBlock.tsx'
import {FilePreview} from '/src/components/inspector/FilePreview.tsx'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.label=t
const source=${JSON.stringify(source)}
function App(){const [state,present]=useState({mode:'markdown',path:'fixture.csv',active:true});window.present=(mode,path='fixture.csv',active=true)=>present({mode,path,active});
if(state.mode==='file')return React.createElement(FilePreview,{path:state.path,root:'/fixture',active:state.active,focusContent:false});
const kind=state.mode.includes('tsv')?'tsv':'csv',value=kind==='tsv'?source.replaceAll(',',String.fromCharCode(9)):source;
if(state.mode.startsWith('tool'))return React.createElement(ToolBlock,{block:{kind:'tool',id:'table',callId:'table',name:'show_widget',at:1,status:'ok',display:{widget:{kind,title:'Table',code:value}}}});
const fence=String.fromCharCode(96).repeat(3);return React.createElement(Markdown,{streaming:state.mode==='pending'},fence+kind+String.fromCharCode(10)+value+(state.mode==='pending'?'':String.fromCharCode(10)+fence))}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],requests=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.route('**/api/v1/file?*',async route=>{
  const path=new URL(route.request().url()).searchParams.get('path');requests.push(path)
  if(path==='bad.csv')return route.fulfill({body:Buffer.from([0xff]),contentType:'text/plain'})
  if(path==='truncated.csv')return route.fulfill({body:'A,B\n1,2\n',headers:{'Content-Range':'bytes 0-7/300000'},status:206})
  if(path==='sales.echarts.json')return route.fulfill({json:chart})
  if(path==='plain.json')return route.fulfill({json:chart})
  return route.fulfill({body:path==='fixture.tsv'?source.replaceAll(',','\t'):source,contentType:'text/plain'})
 })
 await page.goto(server.resolvedUrls.local[0]+'__table-preview')
 await page.evaluate(()=>window.locale('en'))
 await page.locator('tbody tr').first().waitFor()
 assert.equal(await page.locator('tbody tr').count(),50)
 assert.equal(await page.locator('img').count(),0)
 await page.getByRole('button',{name:'Sort table column “Value”',exact:true}).click()
 assert.equal(await page.locator('tbody tr').first().locator('td').first().textContent(),'Beta')
 await page.getByRole('button',{name:'Sort table column “Value”',exact:true}).click()
 assert.equal(await page.locator('tbody tr').first().locator('td').first().textContent(),'Group 119')
 await page.getByRole('textbox',{name:'Filter table',exact:true}).fill('Beta')
 assert.equal(await page.locator('tbody tr').count(),1)
 for(const locale of ['en','zh-CN','en'])for(const [dark,width]of [[false,390],[true,1200]]){
  await page.setViewportSize({width,height:900})
  await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
  assert.equal(await page.getByRole('textbox').inputValue(),'Beta')
  assert.equal(await page.locator('tbody tr').count(),1)
 }
 await page.getByRole('textbox',{name:'Filter table',exact:true}).fill('')
 await page.getByRole('button',{name:'Next table page',exact:true}).click()
 assert.equal(await page.locator('tbody tr').count(),50)
 await page.getByRole('button',{name:'Next table page',exact:true}).click()
 assert.equal(await page.locator('tbody tr').count(),23)
 const pending=page.waitForEvent('download');await page.getByRole('button',{name:'Download',exact:true}).click();const saved=await pending
 const stream=await saved.createReadStream();let downloaded='';for await(const chunk of stream)downloaded+=chunk.toString('utf8')
 assert.equal(downloaded,source)
 await page.evaluate(()=>window.present('pending'))
 await page.getByText('Receiving table data…',{exact:true}).waitFor()
 assert.equal(await page.locator('table').count(),0)
 await page.evaluate(()=>window.present('tool'))
 await page.locator('[data-tool-call-id="table"] tbody tr').first().waitFor()
 assert.equal(await page.locator('tbody tr').count(),50)
 await page.getByRole('checkbox',{name:'First row contains column names',exact:true}).uncheck()
 assert.equal(await page.locator('tbody tr').first().locator('td').first().textContent(),'Name')
 await page.getByRole('checkbox',{name:'First row contains column names',exact:true}).check()
 await page.evaluate(()=>window.present('tsv'))
 await page.getByText('TSV',{exact:true}).waitFor()
 assert.equal(await page.locator('tbody tr').first().locator('td').first().textContent(),'Alpha\t one')
 await page.evaluate(()=>window.present('tool-tsv'))
 await page.locator('[data-tool-call-id="table"] tbody tr').first().waitFor()
 assert.equal(await page.locator('tbody tr').count(),50)
 await page.evaluate(()=>window.present('file','fixture.csv'))
 await page.locator('.file-preview .data-table tbody tr').first().waitFor()
 const sourceLabel=await page.evaluate(()=>window.label('ui.components.inspector.FilePreview.035440298e53'))
 await page.getByRole('button',{name:sourceLabel,exact:true}).first().click()
 await page.locator('[data-file-source]').waitFor()
 await page.getByRole('button',{name:'View rendering',exact:true}).click()
 await page.locator('.data-table tbody tr').first().waitFor()
 const before=requests.length
 await page.evaluate(()=>window.present('file','fixture.csv',false))
 assert.equal(requests.length,before)
 await page.evaluate(()=>window.present('file','sales.echarts.json',false))
 assert.equal(await page.locator('iframe').count(),0)
 await page.evaluate(()=>window.present('file','sales.echarts.json'))
 await page.frameLocator('iframe').locator('#chart[data-chart-ready="true"]').waitFor()
 await page.evaluate(()=>window.present('file','plain.json'))
 await page.locator('[data-file-source]').waitFor()
 assert.equal(await page.locator('iframe').count(),0)
 await page.evaluate(()=>window.present('file','fixture.tsv'))
 await page.locator('.file-preview .data-table tbody tr').first().waitFor()
 assert.equal(await page.locator('tbody tr').first().locator('td').first().textContent(),'Alpha\t one')
 await page.evaluate(()=>window.present('file','truncated.csv'))
 await page.locator('[data-file-source]').waitFor()
 assert.equal(await page.locator('.data-table').count(),0)
 await page.evaluate(()=>window.present('file','bad.csv'))
 await page.getByRole('alert').waitFor()
 assert.equal(await page.locator('.data-table').count(),0)
 assert.deepEqual(errors,[])
 console.log('Tables: real fences/tool/file preview, bounded pages, numeric sorting, filter state, literal HTML, exact CSV download, hidden/file identity, explicit chart suffix, truncated and invalid UTF-8 rejection passed')
},{entry:'/__table-preview-entry.js',vite:{plugins:[{name:'table-preview-fixture',resolveId(id){if(id==='/__table-preview-entry.js')return id},load(id){if(id==='/__table-preview-entry.js')return entry},configureServer(server){server.middlewares.use('/__table-preview',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__table-preview','<html><body><div id="root"></div><script type="module" src="/__table-preview-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
