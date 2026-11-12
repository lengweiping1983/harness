import assert from 'node:assert/strict'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {WorkspacePicker} from '/src/components/overlays/WorkspacePicker.tsx'
import {useAppStore} from '/src/store/app.ts'
import {api} from '/src/lib/api.ts'
import {setLocale,t} from '/src/i18n/index.ts'
import '/src/styles.css'
const project={id:'fixture',path:'/fixture',name:'Project 原文',color:'brand',exists:true}
api.listWorkspaces=async()=>({ok:true,data:{workspaces:[project],default:project.path}})
api.listDirectories=async()=>({ok:true,data:{path:project.path,parent:'/',name:'fixture',entries:Array.from({length:100},(_,index)=>({name:'directory-'+index,path:'/fixture/'+index})),truncated:false}})
const originalFetch=window.fetch
window.pendingChecks=[]
window.releaseChecks=()=>window.pendingChecks.splice(0).forEach(resolve=>resolve(Response.json({cwd:'/fixture',is_git:true,root:'/fixture',branch:'main',head:'abc',refs:['main'],local_branches:['main'],dirty:true})))
window.fetch=(path,options)=>String(path).includes('/api/v1/workspace-options')?new Promise(resolve=>window.pendingChecks.push(resolve)):originalFetch(path,options)
window.copy=t
window.locale=setLocale
window.openPicker=(theme,size)=>{
 useAppStore.getState().setTheme(theme)
 document.documentElement.style.fontSize=size+'px'
 useAppStore.setState({activeSessionId:null,activeWorkspaceId:project.id,workspaces:[project],workspacePickerMode:'select',workspacePickerOpen:true})
}
window.selection=()=>({open:useAppStore.getState().workspacePickerOpen,cwd:useAppStore.getState().newSessionCheckout?.cwd})
createRoot(document.getElementById('root')).render(React.createElement(WorkspacePicker))
`

await browserScenario(async ({server, browser}) => {
  for (const [width, height] of [[647, 676], [390, 676], [1440, 900]]) for (const theme of ['light', 'dark']) for (const size of [10, 16, 22]) {
    const page = await browser.newPage({viewport: {width, height}})
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(server.resolvedUrls.local[0] + '__workspace-picker-height')
    await page.evaluate(({theme, size}) => window.openPicker(theme, size), {theme, size})
    await page.waitForFunction(() => window.pendingChecks.length > 0)
    for (const phase of ['checking', 'ready']) {
      if (phase === 'ready') await page.evaluate(() => window.releaseChecks())
      for (const language of ['en', 'zh-CN', 'en']) {
        await page.evaluate(language => window.locale(language), language)
        const label = await page.evaluate(() => window.copy('ui.newConversation.useProject'))
        const button = page.getByRole('dialog').getByRole('button', {name: label, exact: true})
        await button.waitFor()
        if (phase === 'ready') await page.waitForFunction(label => Array.from(document.querySelectorAll('button')).some(node => node.textContent === label && !node.disabled), label)
        assert.equal(await button.isDisabled(), phase === 'checking')
        assert.equal(await button.evaluate(node => {
          const bounds = node.getBoundingClientRect()
          const x = bounds.left + bounds.width / 2, y = bounds.top + bounds.height / 2
          return bounds.top >= 0 && bounds.bottom <= innerHeight && node.contains(document.elementFromPoint(x, y))
        }), true, JSON.stringify({width, height, theme, size, language, phase}))
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      }
    }
    await page.getByRole('dialog').getByRole('button', {name: await page.evaluate(() => window.copy('ui.newConversation.useProject')), exact: true}).click()
    await page.getByRole('dialog').waitFor({state: 'hidden'})
    assert.deepEqual(await page.evaluate(() => window.selection()), {open: false, cwd: '/fixture'})
    assert.deepEqual(errors, [])
    await page.close()
  }
}, {entry: '/__workspace-picker-height-entry.js', vite: {plugins: [{name: 'workspace-picker-height-fixture',
  resolveId(id) {if (id === '/__workspace-picker-height-entry.js') return id},
  load(id) {if (id === '/__workspace-picker-height-entry.js') return entry},
  configureServer(server) {
    server.middlewares.use('/__workspace-picker-height', async (_req, res, next) => {
      try {
        res.setHeader('Content-Type', 'text/html')
        res.end(await server.transformIndexHtml('/__workspace-picker-height', '<html><body><div id="root"></div><script type="module" src="/__workspace-picker-height-entry.js"></script></body></html>'))
      } catch (error) {next(error)}
    })
  },
}]}})
