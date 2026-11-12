import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React, {useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Markdown} from '/src/components/markdown/Markdown.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
function Fixture() {
  const [source,setSource] = useState('first')
  window.source = setSource
  window.locale = setLocale
  return React.createElement(Markdown,null,'![Original alt 原文](https://media.invalid/'+source+'.png)\\n\\n[Original link 原文](https://example.com/)\\n\\n[Custom](https://example.com/custom "Custom title 原文")')
}
setLocale('en')
createRoot(document.getElementById('root')).render(React.createElement(Fixture))
`
const html = '<!doctype html><html><body><div id="root"></div><script type="module" src="/__markdown-media-entry.js"></script></body></html>'
await browserScenario(async ({server,browser}) => {
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror',error => errors.push(error.message))
  await page.route('https://media.invalid/**',route => route.fulfill({status:200,contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'}))
  await page.goto(server.resolvedUrls.local[0]+'__markdown-media')
  const image = page.getByRole('img',{name:'Original alt 原文',exact:true})
  await image.waitFor()
  await image.evaluate(element => {window.oldImage=element;element.dispatchEvent(new Event('error'))})
  await image.waitFor({state:'detached'})
  await page.evaluate(() => window.source('second'))
  await image.waitFor()
  assert.match(await image.getAttribute('src'),/second\.png$/)
  await page.evaluate(() => window.oldImage.dispatchEvent(new Event('error')))
  assert.equal(await image.count(),1,'An obsolete image error must not hide the new source')
  for (const width of [390,1440]) for (const dark of [false,true]) for (const language of ['en','zh-CN','en']) {
    await page.setViewportSize({width,height:900})
    await page.evaluate(dark => document.documentElement.classList.toggle('dark',dark),dark)
    await page.evaluate(language => window.locale(language),language)
    const link = page.getByRole('link',{name:'Original link 原文',exact:true})
    const expected = language==='en'?'Open in browser: https://example.com/':'在浏览器打开：https://example.com/'
    await page.waitForFunction(expected => document.querySelector('a[href="https://example.com/"]')?.title===expected,expected)
    assert.equal(await link.getAttribute('title'),expected)
    assert.equal(await page.getByRole('link',{name:'Custom',exact:true}).getAttribute('title'),'Custom title 原文')
    assert.equal(await image.getAttribute('alt'),'Original alt 原文')
  }
  await page.evaluate(() => window.source('first'))
  await image.waitFor()
  assert.match(await image.getAttribute('src'),/first\.png$/)
  await image.evaluate(element => element.dispatchEvent(new Event('error')))
  await image.waitFor({state:'detached'})
  assert.deepEqual(errors,[])
  console.log('Markdown: image failure/source replacement/obsolete error and repeated bilingual link titles passed')
},{entry:'/__markdown-media-entry.js',vite:{plugins:[{name:'markdown-media-fixture',resolveId(id){if(id==='/__markdown-media-entry.js')return id},load(id){if(id==='/__markdown-media-entry.js')return entry},configureServer(server){server.middlewares.use('/__markdown-media',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__markdown-media',html))}catch(error){next(error)}})}}]}})
