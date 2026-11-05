import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

/** 探针不参与正文布局，避免 iframe 增高归零滚动后把按下与松开投递给不同节点。 */
async function installEvalProbe(frame){
 const geometry=await frame.locator('body').evaluate(()=>{
  const before=document.body.scrollHeight,button=document.createElement('button'),events=[]
  button.id='eval-probe';button.textContent='CSP probe'
  button.style.cssText='position:fixed;top:0;left:0;z-index:2147483647;margin:0'
  button.setAttribute('onclick',"this.dataset.trusted=String(event.isTrusted);try{Function('return 1')();this.dataset.result='allowed'}catch(error){this.dataset.result='blocked';this.dataset.error=error.name;this.dataset.message=error.message}this.dataset.completed='true'")
  const record=event=>events.push({type:event.type,target:event.target?.id||event.target?.nodeName,scrollY,height:innerHeight})
  const violation=event=>{
   events.push({type:'csp',blocked:event.blockedURI,directive:event.effectiveDirective,disposition:event.disposition})
   if(event.blockedURI==='eval'){
    button.dataset.cspBlocked=event.blockedURI;button.dataset.cspDisposition=event.disposition
   }
  }
  for(const type of ['pointerdown','pointerup','click'])addEventListener(type,record,true)
  addEventListener('securitypolicyviolation',violation)
  button.probeEvents=events
  button.cleanupProbe=()=>{
   for(const type of ['pointerdown','pointerup','click'])removeEventListener(type,record,true)
   removeEventListener('securitypolicyviolation',violation)
  }
  document.body.append(button)
  const rect=button.getBoundingClientRect()
  return {before,after:document.body.scrollHeight,inViewport:rect.top>=0&&rect.left>=0&&rect.bottom<=innerHeight&&rect.right<=innerWidth}
 })
 assert.equal(geometry.after,geometry.before,'The CSP probe must not resize the sandbox')
 assert.equal(geometry.inViewport,true,'The CSP probe must start inside the sandbox viewport')
 return frame.locator('#eval-probe')
}

async function assertEvalBlocked(frame,probe,label){
 const result=await probe.evaluate(node=>({trusted:node.dataset.trusted,completed:node.dataset.completed,result:node.dataset.result,error:node.dataset.error,message:node.dataset.message,events:node.probeEvents}))
 const diagnostic=label+': '+JSON.stringify(result)
 assert.equal(result.trusted,'true','The CSP probe must receive a trusted click; '+diagnostic)
 assert.equal(result.completed,'true','The CSP probe must finish; '+diagnostic)
 assert.equal(result.result,'blocked','Dynamic evaluation must be rejected; '+diagnostic)
 assert.equal(result.error,'EvalError','An unrelated exception is not CSP rejection; '+diagnostic)
 // CSP 违规事件异步投递；等待实际拒绝证据，而不是固定睡眠或仅接受任意异常。
 await frame.locator('#eval-probe[data-csp-blocked="eval"]').waitFor({state:'attached',timeout:5000})
 assert.equal(await probe.getAttribute('data-csp-disposition'),'enforce')
 await probe.evaluate(node=>{node.cleanupProbe();node.remove()})
 return result.events
}

/** 固定在按下后释放真实高度回传，覆盖旧探针松开时命中 Canvas、结果未赋值的交错。 */
async function probeDuringResize(page,frame){
 const iframe=page.locator('[data-tool-call-id="chart"] iframe')
 await iframe.evaluate(node=>{node.style.maxHeight=node.clientHeight+'px'})
 const probe=await installEvalProbe(frame)
 await frame.locator('body').evaluate(()=>{const spacer=document.createElement('div');spacer.id='probe-resize';spacer.style.height='40px';document.body.append(spacer)})
 await page.waitForFunction(()=>{const node=document.querySelector('[data-tool-call-id="chart"] iframe');return parseFloat(node.style.height)>parseFloat(node.style.maxHeight)})
 await probe.scrollIntoViewIfNeeded()
 const box=await probe.boundingBox()
 assert.ok(box)
 await page.mouse.move(box.x+box.width/2,box.y+box.height/2)
 await page.mouse.down()
 const expected=await iframe.evaluate(node=>{node.style.maxHeight='';return parseFloat(node.style.height)})
 await page.waitForFunction(height=>document.querySelector('[data-tool-call-id="chart"] iframe').clientHeight===height,expected)
 await page.mouse.up()
 const events=await assertEvalBlocked(frame,probe,'chartjs resize between pointerdown and pointerup')
 const down=events.find(event=>event.type==='pointerdown'),up=events.find(event=>event.type==='pointerup')
 assert.equal(down?.target,'eval-probe');assert.equal(up?.target,'eval-probe')
 assert.ok(up.height>down.height,'The iframe must actually grow between pointerdown and pointerup')
 await frame.locator('#probe-resize').evaluate(node=>node.remove())
}

const specs={
 echarts:{xAxis:{type:'category',data:['A','B']},yAxis:{type:'value'},series:[{name:'Plan',type:'bar',data:[3,8]},{name:'Actual',type:'bar',data:[2,5]}],legend:{},tooltip:{}},
 chartjs:{type:'bar',data:{labels:['A','B'],datasets:[{label:'Count',data:[3,8]}]}},
 plotly:{data:[{name:'Plan',x:['A','B'],y:[3,8],type:'bar'},{name:'Actual',x:['A','B'],y:[2,5],type:'bar'}]},
 'vega-lite':{params:[{name:'threshold',value:0,bind:{input:'range',min:0,max:10,step:1}}],transform:[{filter:'datum.value>=threshold'}],data:{values:[{label:'A',value:3},{label:'B',value:8}]},mark:'bar',encoding:{x:{field:'label',type:'nominal'},y:{field:'value',type:'quantitative'}}},
 vega:{signals:[{name:'pointSize',value:20,bind:{input:'range',min:20,max:100,step:1}}],width:300,height:200,data:[{name:'table',values:[{x:30,y:40},{x:150,y:80}]}],marks:[{type:'symbol',from:{data:'table'},encode:{enter:{x:{field:'x'},y:{field:'y'},fill:{value:'teal'}},update:{size:{signal:'pointSize'}}}}]},
}
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Markdown} from '/src/components/markdown/MarkdownContent.tsx'
import {ToolBlock} from '/src/components/chat/blocks/ToolBlock.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale
function App(){const [value,draw]=useState(null);window.draw=(kind,spec,closed=true,tool=false)=>draw({kind,spec,closed,tool});if(!value)return null;
const source=JSON.stringify(value.spec);return value.tool?React.createElement(ToolBlock,{block:{kind:'tool',id:'chart',callId:'chart',at:1,name:'show_widget',status:'ok',display:{widget:{kind:value.kind,title:value.kind,code:source,chars:source.length}}}}):React.createElement(Markdown,{streaming:!value.closed},String.fromCharCode(96).repeat(3)+value.kind+'\\n'+source+(value.closed?'\\n'+String.fromCharCode(96).repeat(3):''))}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
await browserScenario(async({server,browser})=>{
 const page=await browser.newPage(),errors=[],external=[],runtimeRequests=[],timings=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('request',request=>{if(new URL(request.url()).searchParams.has('raw'))runtimeRequests.push(request.url());if(!request.url().startsWith(server.resolvedUrls.local[0])&&!request.url().startsWith('data:'))external.push(request.url())})
 await page.goto(server.resolvedUrls.local[0]+'__chart-spec')
 for(const [kind,spec]of Object.entries(specs)){
  const requested=runtimeRequests.length
  await page.evaluate(({kind,spec})=>window.draw(kind,spec,false),{kind,spec})
  await page.getByRole('status').waitFor()
  assert.equal(await page.locator('iframe').count(),0)
  assert.equal(runtimeRequests.length,requested,'Open fences must not download chart runtimes')
  const started=performance.now()
  await page.evaluate(({kind,spec})=>window.draw(kind,spec),{kind,spec})
  const frame=page.frameLocator('iframe')
  await frame.locator('#chart[data-chart-ready="true"]').waitFor()
  await page.locator('[aria-busy="false"] > iframe').waitFor()
  timings.push({kind,readyMs:Math.round(performance.now()-started)})
  assert.ok(await frame.locator('svg,canvas').count()>0,kind+' must render actual chart geometry')
  await frame.locator('#chart').evaluate(node=>window.chartNode=node)
  const child=page.frames().find(value=>value.parentFrame())
  if(kind==='echarts'){
   await frame.getByText('Plan',{exact:true}).click()
   await child.waitForFunction(()=>echarts.getInstanceByDom(document.getElementById('chart')).getOption().legend[0].selected.Plan===false)
  }else if(kind==='chartjs'){
   const box=await frame.locator('canvas').evaluate(node=>Chart.getChart(node).legend.legendHitBoxes[0])
   await frame.locator('canvas').click({position:{x:box.left+box.width/2,y:box.top+box.height/2}})
   await child.waitForFunction(()=>!Chart.getChart(document.querySelector('canvas')).isDatasetVisible(0))
  }else if(kind==='plotly'){
   await frame.locator('.legendtoggle').first().click()
   await child.waitForFunction(()=>document.getElementById('chart').data[0].visible==='legendonly')
  }else{
   const before=kind==='vega'?await frame.locator('.mark-symbol path').first().getAttribute('d'):null
   await frame.locator('input[type=range]').focus()
   await frame.locator('input[type=range]').press('End')
   if(kind==='vega-lite')await child.waitForFunction(()=>document.querySelectorAll('.mark-rect path').length===0)
   else await child.waitForFunction(value=>document.querySelector('.mark-symbol path').getAttribute('d')!==value,before)
  }
  console.log('Native control passed:',kind)

  for(const locale of ['en','zh-CN','en'])for(const [dark,width]of [[false,390],[true,1200]]){
   await page.setViewportSize({width,height:850})
   await page.evaluate(async({locale,dark})=>{await window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   await page.locator('[aria-busy="false"] > iframe').waitFor()
   assert.equal(await frame.locator('#chart').evaluate(node=>node===window.chartNode),true)
   if(kind==='echarts')assert.equal(await frame.locator('#chart').evaluate(node=>echarts.getInstanceByDom(node).getOption().legend[0].selected.Plan),false)
   if(kind==='chartjs')assert.equal(await frame.locator('canvas').evaluate(node=>Chart.getChart(node).isDatasetVisible(0)),false)
   if(kind==='plotly')assert.equal(await frame.locator('#chart').evaluate(node=>node.data[0].visible),'legendonly')
   if(kind==='vega'||kind==='vega-lite')assert.equal(await frame.locator('input[type=range]').inputValue(),kind==='vega'?'100':'10')
  }
  await page.getByRole('button',{name:'View code',exact:true}).click()
  await page.getByRole('button',{name:'View rendering',exact:true}).click()
  await page.locator('[aria-busy="false"] > iframe').waitFor()
  assert.equal(await frame.locator('#chart').evaluate(node=>node===window.chartNode),true)
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Download',exact:true}).click()
  assert.equal((await download).suggestedFilename(),kind+'.'+kind+'.json')
  await page.evaluate(({kind,spec})=>window.draw(kind,spec,true,true),{kind,spec})
  await page.locator('[data-tool-call-id="chart"] iframe').waitFor()
  await frame.locator('#chart[data-chart-ready="true"]').waitFor()
  await page.locator('[aria-busy="false"] > iframe').waitFor()
  assert.equal(await frame.locator('body').evaluate(()=>{try{return !!parent.document}catch{return false}}),false)
  const probe=await installEvalProbe(frame)
  await probe.click()
  await assertEvalBlocked(frame,probe,kind)
  if(kind==='chartjs')await probeDuringResize(page,frame)

 }
 await page.evaluate(()=>window.draw('vega-lite',{data:{url:'https://example.com/data.json'},mark:'bar'}))
 await page.getByRole('alert').waitFor();assert.equal(await page.locator('iframe').count(),0)
 assert.equal(runtimeRequests.length,6,'Vega and Vega-Lite must share the same three local runtime chunks')
 console.log('Local chart cold/warm readiness (ms):',JSON.stringify(timings))
 assert.deepEqual(external,[])
 assert.deepEqual(errors,[])
 console.log('Charts: five real local engines, fences and tool results, unfinished JSON, no network/eval/host access, language/theme/width state, source toggle, original JSON download and explicit resource rejection passed')
},{entry:'/__chart-spec-entry.js',vite:{plugins:[{name:'chart-spec-fixture',resolveId(id){if(id==='/__chart-spec-entry.js')return id},load(id){if(id==='/__chart-spec-entry.js')return entry},configureServer(server){server.middlewares.use('/__chart-spec',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__chart-spec','<html><body><div id="root"></div><script type="module" src="/__chart-spec-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
