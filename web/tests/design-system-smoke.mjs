import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'
const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Button, Switch, Section, Badge} from '/src/components/ui/primitives.tsx'
import {ComposerCard, ComposerEditor, ComposerToolbar, ComposerSendButton} from '/src/components/chat/ComposerSurface.tsx'
import {applyTheme} from '/src/lib/theme.ts'
import {setLocale, t} from '/src/i18n/index.ts'
import '/src/styles.css'
window.configure = async ({language, theme, size}) => {await setLocale(language); applyTheme(theme); document.documentElement.style.fontSize=size+'px'; root.render(React.createElement(Fixture))}
function Fixture(){return React.createElement('main',{className:'p-4 space-y-4'},
 React.createElement('h2',{className:'ui-section-title'},t('ui.context.settings')),
 React.createElement(Section,{title:t('ui.components.chat.blocks.HookStatisticsDialog.909ee3161e8a')},
 React.createElement('h3',{className:'ui-card-title'},t('ui.context.settings')),
 React.createElement('input',{className:'ui-field','aria-label':'Field'}),
 React.createElement('select',{className:'ui-field','aria-label':'Select'},React.createElement('option',null,'Original 原文')),
 React.createElement('input',{type:'range','aria-label':'Range'}),
 React.createElement(Switch,{'aria-label':'Switch',defaultChecked:true}),
 ...['brand','ok','warn','danger'].map(tone=>React.createElement(Badge,{key:tone,tone},tone))),
 React.createElement(ComposerCard,null,React.createElement(ComposerEditor),React.createElement(ComposerToolbar,null,React.createElement(Button,{size:'sm'},'Original'),React.createElement(ComposerSendButton))))}
const root=createRoot(document.getElementById('root'))
`
function luminance(rgb) {
 const v=rgb.map(x=>{x/=255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4})
 return v[0]*.2126+v[1]*.7152+v[2]*.0722
}
function contrast(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)}
await browserScenario(async({server,browser})=>{
 for(const language of ['en','zh-CN'])for(const theme of ['light','dark'])for(const width of [390,1440])for(const size of [10,16,22]){
  const page=await browser.newPage({viewport:{width,height:1000}})
  await page.goto(server.resolvedUrls.local[0]+'__design-system')
  await page.evaluate(config=>window.configure(config),{language,theme,size})
  await page.locator('.composer-editor').waitFor()
  const values=await page.evaluate(()=>{
   const style=getComputedStyle(document.documentElement),token=name=>style.getPropertyValue('--c-'+name).trim().split(/\s+/).map(Number)
   return {tokens:Object.fromEntries(['canvas','surface','raised','overlay','ink','muted','faint','brand','brand-ink','ok','warn','danger'].map(name=>[name,token(name)])),overflow:document.documentElement.scrollWidth>innerWidth,
    fonts:['.ui-section-title','.ui-card-title','.ui-group-title'].map(selector=>parseFloat(getComputedStyle(document.querySelector(selector)).fontSize))}
  })
  assert.equal(values.overflow,false)
  assert.deepEqual(values.fonts,[size,size*.875,size*.75])
  for(const foreground of ['ink','muted','faint','brand','ok','warn','danger'])for(const surface of ['canvas','surface','raised','overlay'])
   assert.ok(contrast(values.tokens[foreground],values.tokens[surface])>=4.5,theme+' '+foreground+'/'+surface)
  assert.ok(contrast(values.tokens['brand-ink'],values.tokens.brand)>=4.5)
  const badges=await page.locator('section span').evaluateAll(nodes=>nodes.map(node=>{const s=getComputedStyle(node);return {color:s.color.match(/[\d.]+/g).map(Number),background:s.backgroundColor.match(/[\d.]+/g).map(Number)}}))
  for(const badge of badges){const alpha=badge.background[3]??1;const background=badge.background.slice(0,3).map((v,i)=>v*alpha+values.tokens.canvas[i]*(1-alpha));assert.ok(contrast(badge.color,background)>=4.5,theme+' badge '+JSON.stringify(badge))}
  const select=page.getByLabel('Select');await select.focus()
  await page.waitForFunction(()=>{const s=getComputedStyle(document.querySelector('select'));return s.outlineStyle==='solid'&&s.outlineWidth==='2px'})
  const focus=await select.evaluate(node=>{const s=getComputedStyle(node);return {outline:s.outlineColor,offset:s.outlineOffset,shadow:s.boxShadow,accent:getComputedStyle(document.querySelector('input[type=range]')).accentColor}})
  assert.equal(focus.offset,'-2px');assert.equal(focus.shadow,'none');assert.equal(focus.accent,focus.outline)
  const title=await page.locator('.ui-group-title').textContent()
  if(language==='zh-CN')assert.equal(title,'钩子统计信息')
  const send=page.locator('.composer-card button').last()
  assert.equal(await send.evaluate(node=>node.getBoundingClientRect().height),size*2.25)
  if(width===390&&size===16)await page.screenshot({path:'../output/design-system-'+language+'-'+theme+'.png',fullPage:true})
  await page.close()
 }
 console.log('Design system: real shared controls, 24 language/theme/width/font configurations, text contrast and keyboard focus passed')
},{entry:'/__design-system-entry.js',vite:{plugins:[{name:'design-system-fixture',resolveId(id){if(id==='/__design-system-entry.js')return id},load(id){if(id==='/__design-system-entry.js')return entry},configureServer(server){server.middlewares.use('/__design-system',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__design-system','<html><body><div id="root"></div><script type="module" src="/__design-system-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
