import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Conversation} from '/src/components/chat/Conversation.tsx'
import {ConversationSuggestions} from '/src/components/chat/ConversationSuggestions.tsx'
import {QuestionCard} from '/src/components/chat/blocks/QuestionCard.tsx'
import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx'
import {useAppStore} from '/src/store/app.ts'
import {setLocale,useLocale} from '/src/i18n/index.ts'
import {noticeText} from '/src/lib/noticeText.ts'
import '/src/styles.css'
const question={id:'original-permission',kind:'question',questionId:'original-permission',at:1,
  questions:[{id:'permission',question:'Original external permission',options:[{id:'external',label:'拒绝',description:'Original external description'}]}]}
window.answers=[];window.suggestions=[]
function Fixture({view}) {
  useLocale()
  return view==='settings'?React.createElement(SettingsDialog):view==='question'
    ?React.createElement(QuestionCard,{block:question,sessionId:null,onAnswer:(id,answers)=>window.answers.push({id,answers})})
    :React.createElement(React.Fragment,null,
      React.createElement(Conversation,{blocks:[],running:false,streaming:false,connection:'open'}),
      React.createElement(ConversationSuggestions,{onChoose:text=>window.suggestions.push(text)}))
}
const root=createRoot(document.getElementById('root'))
window.configure=(view,language,theme)=>{
  setLocale(language);document.documentElement.className=theme
  useAppStore.setState({settingsOpen:view==='settings',settingsTarget:'shortcuts'})
  root.render(React.createElement(Fixture,{view}))
}
window.failLanguage=async()=>{await useAppStore.getState().setLanguage('en');return noticeText(useAppStore.getState().languageError)}
window.configure('conversation','en','light')
`
const html='<!doctype html><html><body><div id="root"></div><script type="module" src="/__static-copy-entry.js"></script></body></html>'
const catalogs=Object.fromEntries(await Promise.all(['en','zh-CN'].map(async language=>[language,JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url)))])))
const suggestions=['ui.components.chat.Conversation.b2b6ca2ea1f1','ui.components.chat.Conversation.8eb6b977850f','ui.components.chat.Conversation.0ac196393e3f','ui.components.chat.Conversation.20061c1959d0']
await browserScenario(async({server,browser})=>{
  for(const initial of ['en','zh-CN'])for(const theme of ['light','dark'])for(const width of [390,1440]){
    const page=await browser.newPage({viewport:{width,height:900}}),errors=[]
    page.on('pageerror',error=>errors.push(error.message))
    await page.route('**/api/v1/**',route=>route.fulfill({status:503,json:{code:route.request().method()==='PATCH'?'settings/persist-failed':'fixture/unavailable',message:'Original server text'}}))
    await page.goto(server.resolvedUrls.local[0]+'__static-copy')
    for(const language of [initial,initial==='en'?'zh-CN':'en',initial]){
      const strings=catalogs[language]
      await page.evaluate(({language,theme})=>window.configure('conversation',language,theme),{language,theme})
      await page.getByRole('heading',{name:strings['ui.components.chat.Conversation.e33a219fc51f'],exact:true}).waitFor()
      const cards=page.locator('.welcome-suggestions button')
      assert.deepEqual(await cards.allTextContents(),suggestions.map(key=>strings[key]))
      await cards.first().click()
      assert.equal(await page.evaluate(()=>window.suggestions.at(-1)),strings[suggestions[0]])
      const scenarioButtons=page.locator('.welcome-scenarios button')
      assert.equal(await scenarioButtons.count(),3)
      const scenarioText=await page.locator('.welcome-scenarios').textContent()
      for(const key of ['5f3a9c1e8d2b','7c4d2e9a6f18','9e5f1b7c3a46','3d8a2c6e4b97','6b1e4d8a2c59','2c7f5a9d1e83']) assert.ok(scenarioText.includes(strings['ui.components.chat.Conversation.'+key]),key)
      await page.evaluate(({language,theme})=>window.configure('settings',language,theme),{language,theme})
      for(const group of ['agents','connections','advanced']) {const button=page.getByRole('button',{name:strings['ui.settings.groups.'+group],exact:true});if(await button.getAttribute('aria-expanded')==='false')await button.click()}
      for(const key of ['ui.memory.heading','ui.settings.tools.title','ui.agents.directory','ui.extensions.skills','ui.extensions.connectors','ui.extensions.plugins']){
        await page.getByRole('button',{name:strings[key],exact:true}).waitFor()
      }
      assert.equal(await page.evaluate(()=>window.failLanguage()),strings['ui.settings.persistFailed'])
      await page.evaluate(({language,theme})=>window.configure('question',language,theme),{language,theme})
      const original=page.getByRole('button',{name:'拒绝',exact:true})
      await original.waitFor();await original.click()
      await page.getByRole('button',{name:strings['ui.components.chat.blocks.QuestionCard.0c1762dddc4f'],exact:true}).click()
      assert.deepEqual(await page.evaluate(()=>window.answers.at(-1)),{id:'original-permission',answers:[{id:'permission',selected:['拒绝']}]})
      assert.ok(await page.locator('body').evaluate(element=>element.scrollWidth<=innerWidth+1))
    }
    assert.deepEqual(errors,[])
    await page.close()
  }
  console.log('Static copy: repeated bilingual suggestions, scenario entries and click text, settings labels, keyed persistence errors and untouched external permission answers across eight layouts passed')
},{entry:'/__static-copy-entry.js',vite:{plugins:[{name:'static-copy-fixture',resolveId(id){if(id==='/__static-copy-entry.js')return id},load(id){if(id==='/__static-copy-entry.js')return entry},configureServer(server){server.middlewares.use('/__static-copy',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__static-copy',html))}catch(error){next(error)}})}}]}})
