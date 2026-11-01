import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {FileTypeIcon} from '/src/components/ui/FileTypeIcon.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
const root=createRoot(document.getElementById('root'))
window.configure=(language,theme)=>{
 setLocale(language);document.documentElement.className=theme
 root.render(React.createElement(React.Fragment,null,
  React.createElement('div',{hidden:true},React.createElement(FileTypeIcon,{path:'next.config.js'})),
  React.createElement('div',{'data-visible':true},React.createElement(FileTypeIcon,{path:'next.config.js'}),React.createElement(FileTypeIcon,{path:'next.config.js'}))))
}
window.configure('en','light')
`
const html='<html><body><div id="root"></div><script type="module" src="/__icon-identity-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
 for(const theme of ['light','dark'])for(const width of [390,1440]){
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.goto(server.resolvedUrls.local[0]+'__icon-identity')
  let original
  for(const language of ['en','zh-CN','en']){
   await page.evaluate(({language,theme})=>window.configure(language,theme),{language,theme})
   await page.locator('linearGradient').first().waitFor({state:'attached'})
   const values=await page.locator('svg').evaluateAll(elements=>elements.map(svg=>{
    const gradient=svg.querySelector('linearGradient'),path=svg.querySelector('[fill^="url("]')
    return {id:gradient?.id,reference:path?.getAttribute('fill'),owned:document.getElementById(gradient?.id)?.ownerSVGElement===svg}
   }))
   assert.equal(values.length,3)
   assert.equal(new Set(values.map(value=>value.id)).size,3)
   for(const value of values){assert.ok(value.owned);assert.equal(value.reference,'url(#'+value.id+')')}
   if(original)assert.deepEqual(values,original)
   original=values
  }
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Icon identity: hidden and visible repeated SVG gradients own distinct stable references across bilingual switches, themes and widths passed')
},{entry:'/__icon-identity-entry.js',vite:{plugins:[{name:'icon-identity-fixture',resolveId(id){if(id==='/__icon-identity-entry.js')return id},load(id){if(id==='/__icon-identity-entry.js')return entry},configureServer(server){server.middlewares.use('/__icon-identity',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__icon-identity',html))}catch(error){next(error)}})}}]}})
