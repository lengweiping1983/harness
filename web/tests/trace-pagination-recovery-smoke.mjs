import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const row=(id,time)=>({id,kind:'request',time,order:1,name:'fixture model',status:'completed',request:{id:'trace-'+id,purpose:'agent'}})
const entry=`
import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ExecutionTracePanel} from '/src/components/inspector/ExecutionTracePanel.tsx'
import {api} from '/src/lib/api.ts'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.calls=[];window.pending=[]
api.executionTrace=(owner,_scope,after)=>new Promise(resolve=>{
 window.calls.push({owner,after})
 if(owner==='owner'&&!after) resolve({ok:true,data:{entries:[${JSON.stringify(row('owner-1',1))}],has_more:true,next_after:'cursor-owner',context_missing:false,session_id:owner}})
 else if(owner==='other'&&!after) resolve({ok:true,data:{entries:[${JSON.stringify(row('other-1',2))}],has_more:false,next_after:null,context_missing:false,session_id:owner}})
 else window.pending.push({owner,resolve})
})
function Fixture(){const [owner,setOwner]=useState('owner');window.switchOwner=()=>setOwner('other');window.resolvePending=()=>window.pending.shift()?.resolve({ok:true,data:{entries:[],has_more:false,next_after:null,context_missing:false,session_id:'owner'}});return React.createElement(ExecutionTracePanel,{sessionId:owner})}
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`

await browserScenario(async({server,browser})=>{
 const page=await browser.newPage({viewport:{width:700,height:700}}),errors=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto(server.resolvedUrls.local[0]+'__trace-pagination-recovery')
 await page.waitForFunction(()=>window.calls.length===1)
 await page.getByRole('button',{name:'Show more',exact:true}).click()
 await page.waitForFunction(()=>window.pending.length===1)
 await page.evaluate(()=>window.switchOwner())
 await page.evaluate(()=>window.resolvePending())
 await page.getByRole('button',{name:'Refresh request and tool log',exact:true}).click()
 await page.waitForFunction(()=>window.calls.length>=2)
 assert.equal(await page.evaluate(()=>window.calls.at(-1).owner),'other')
 await page.getByText('fixture model',{exact:true}).waitFor()
 assert.deepEqual(errors,[])
 await page.close()
 console.log('Execution trace pagination: stale show-more responses release the read lock across session changes')
},{entry:'/__trace-pagination-recovery-entry.js',vite:{plugins:[{
 name:'trace-pagination-recovery-fixture',resolveId(id){if(id==='/__trace-pagination-recovery-entry.js')return id},load(id){if(id==='/__trace-pagination-recovery-entry.js')return entry},configureServer(server){server.middlewares.use('/__trace-pagination-recovery',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__trace-pagination-recovery','<html><body><div id="root"></div><script type="module" src="/__trace-pagination-recovery-entry.js"></script></body></html>'))}catch(error){next(error)}})}
}]}})
