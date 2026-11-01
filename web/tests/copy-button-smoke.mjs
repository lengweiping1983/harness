import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'
const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {CopyButton} from '/src/components/ui/primitives.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale
const root=createRoot(document.getElementById('root'))
window.mount=(text='fixture copy',lazy=false)=>{
  const props={text:lazy?()=>text:text,...(lazy?{contentIdentity:text}:{}),label:'Copy fixture'}
  root.render(React.createElement(React.StrictMode,null,React.createElement('div',{'data-copy-source':text},React.createElement(CopyButton,props))))
}
window.hide=()=>root.render(null)
window.mount()
`
const html='<html><body><div id="root"></div><script type="module" src="/__copy-entry.js"></script></body></html>'
await browserScenario(async({server,browser})=>{
  for(const locale of ['zh-CN','en'])for(const theme of ['light','dark'])for(const width of [390,1440]){
    const strings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url)))
    const page=await browser.newPage({viewport:{width,height:900}})
    const errors=[]
    page.on('pageerror',error=>errors.push(error.message))
    await page.addInitScript(()=>{
      window.mode='denied';window.calls=0
      Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{
        window.calls++
        if(window.mode==='pending') return new Promise(resolve=>{window.finish=resolve})
        if(window.mode==='denied') throw Error('fixture denied')
      }}})
      document.execCommand=()=>false
    })
    await page.goto(server.resolvedUrls.local[0]+'__copy')
    await page.evaluate(({locale,theme})=>{window.locale(locale);document.documentElement.className=theme},{locale,theme})
    const button=page.getByRole('button',{name:'Copy fixture',exact:true})
    const status=page.getByRole('status')
    await button.click()
    await status.filter({hasText:strings['ui.components.chat.blocks.MessageActions.753d8bb0da99']}).waitFor({state:'attached'})
    assert.equal(await button.isVisible(),true)
    await page.evaluate(()=>{window.mode='success'})
    await page.clock.install()
    await button.click()
    const success=strings['ui.components.ui.primitives.8f6f8d979c98']
    await status.filter({hasText:success}).waitFor({state:'attached'})
    await page.clock.runFor(1000)
    await button.click()
    await page.clock.runFor(700)
    assert.equal(await status.textContent(),success)
    await page.clock.runFor(1000)
    assert.equal(await status.textContent(),'')
    for(const lazy of [false,true]){
      await page.evaluate(lazy=>{window.mount('source A',lazy);window.mode='pending';window.calls=0},lazy)
      await button.click()
      await page.waitForFunction(()=>typeof window.finish==='function' && window.calls===1)
      await page.evaluate(lazy=>window.mount('source B',lazy),lazy)
      await page.locator('[data-copy-source="source B"]').waitFor()
      await page.evaluate(()=>window.finish())
      await page.clock.runFor(1)
      assert.equal(await status.textContent(),'','Old copy success cannot confirm replacement content')
      assert.equal(await page.evaluate(()=>window.calls),1)
      await page.evaluate(()=>{window.mode='success'})
      await button.click()
      await status.filter({hasText:success}).waitFor({state:'attached'})
      await page.evaluate(lazy=>window.mount('source C',lazy),lazy)
      await page.locator('[data-copy-source="source C"]').waitFor()
      await page.clock.runFor(1)
      assert.equal(await status.textContent(),'','Replacement content clears old success feedback')
      await page.evaluate(lazy=>{window.mount('source A',lazy);window.mode='pending';window.calls=0},lazy)
      await page.locator('[data-copy-source="source A"]').waitFor()
      await button.click()
      await page.evaluate(lazy=>window.mount('source B',lazy),lazy)
      await page.locator('[data-copy-source="source B"]').waitFor()
      await page.evaluate(lazy=>window.mount('source A',lazy),lazy)
      await page.locator('[data-copy-source="source A"]').waitFor()
      await page.evaluate(()=>window.finish())
      await page.clock.runFor(1)
      assert.equal(await status.textContent(),'','Returning to A cannot revive the old A copy result')
      assert.equal(await page.evaluate(()=>window.calls),1)
      await page.evaluate(()=>{window.mode='success'})
      await button.click()
      await status.filter({hasText:success}).waitFor({state:'attached'})
      const otherLocale=locale==='en'?'zh-CN':'en'
      const otherStrings=JSON.parse(await readFile(new URL('../src/i18n/locales/'+otherLocale+'.json',import.meta.url)))
      await page.evaluate(({lazy,otherLocale})=>{window.locale(otherLocale);window.mount('source A',lazy)},{lazy,otherLocale})
      await status.filter({hasText:otherStrings['ui.components.ui.primitives.8f6f8d979c98']}).waitFor({state:'attached'})
      assert.equal(await page.evaluate(()=>window.calls),2)
      await page.evaluate(locale=>window.locale(locale),locale)
    }
    await page.evaluate(()=>{window.mode='pending';window.calls=0})
    await button.click();await button.click()
    assert.equal(await page.evaluate(()=>window.calls),1)
    await page.evaluate(()=>window.hide())
    await page.evaluate(()=>window.finish())
    await page.clock.runFor(2000)
    assert.equal(await page.getByRole('status').count(),0)
    assert.deepEqual(errors,[])
    await page.close()
  }
  console.log('Copy button: denial, recovery, stable accessible label, repeated timer, concurrent lock, replacement/ABA ownership, locale-only rerender and unmount passed')
},{entry:'/__copy-entry.js',vite:{plugins:[{name:'copy-fixture',resolveId(id){if(id==='/__copy-entry.js')return id},load(id){if(id==='/__copy-entry.js')return entry},configureServer(server){server.middlewares.use('/__copy',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__copy',html))}catch(error){next(error)}})}}]}})
