import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import ts from 'typescript'
import {browserScenario} from './browser-scenario.mjs'
const catalogs=Object.fromEntries(['en','zh-CN'].map(locale=>[locale,JSON.parse(readFileSync(new URL('../src/i18n/locales/'+locale+'.json',import.meta.url),'utf8'))]))
const parse=path=>ts.createSourceFile(path,readFileSync(new URL(path,import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,path.endsWith('tsx')?ts.ScriptKind.TSX:ts.ScriptKind.TS)
const source=parse('../src/components/overlays/AgentDefinitionCreator.tsx'),helpers=parse('../src/lib/teamAuthoring.ts')
const outputs=name=>{
 const values=new Set()
 const collect=node=>{if(ts.isStringLiteralLike(node))values.add(node.text);else if(ts.isConditionalExpression(node)){collect(node.whenTrue);collect(node.whenFalse)}}
 const walk=node=>{if(ts.isReturnStatement(node)&&node.expression)collect(node.expression);ts.forEachChild(node,walk)}
 for(const node of helpers.statements)if(ts.isFunctionDeclaration(node)&&node.name?.text===name)walk(node)
 assert.ok(values.size,name);return [...values]
}
const resolve=node=>{
 if(ts.isStringLiteralLike(node))return [node.text]
 if(ts.isConditionalExpression(node))return [...resolve(node.whenTrue),...resolve(node.whenFalse)]
 if(ts.isIdentifier(node)&&node.text==='memberIssue')return ['memberJsonInvalid',...outputs('teamMemberIssue')]
 if(ts.isBinaryExpression(node)&&node.operatorToken.kind===ts.SyntaxKind.BarBarToken)return [...resolve(node.left),...resolve(node.right)]
 if(ts.isCallExpression(node)&&node.expression.getText(source)==='memberRenameIssue')return outputs('memberRenameIssue')
 throw Error('Unresolved authoring resource argument: '+node.getText(source))
}
const keys=new Set()
const walk=node=>{if(ts.isCallExpression(node)&&ts.isIdentifier(node.expression)&&node.expression.text==='label')for(const key of resolve(node.arguments[0]))keys.add('ui.agent.author.'+key);ts.forEachChild(node,walk)}
walk(source)
for(const locale of ['en','zh-CN'])for(const key of keys)assert.ok(typeof catalogs[locale][key]==='string'&&catalogs[locale][key],locale+': '+key)
const entry=`
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AgentDefinitionCreator} from '/src/components/overlays/AgentDefinitionCreator.tsx'
import {setLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
setLocale('en');window.locale=setLocale;window.refreshes=0
const view=new URLSearchParams(location.search).get('view'),kind=view.startsWith('team')?'team':'agent'
const seed={name:'fixture',display_name:'Original name 原文',description:'Original description 原文',agent_type:kind,system_prompt:'Original instructions 原文',skills:[],metadata:{},members:kind==='team'?[{name:'coordinator',role:'lead',prompt:'Original lead 原文'},{name:'worker',role:'member',prompt:'Original member 原文'},{name:'external',role:'member',runner:'codex',prompt:'Original external 原文'}]:[],...(view.endsWith('edit')?{edit_revision:'revision',edit_scope:'user'}:{})}
createRoot(document.getElementById('root')).render(React.createElement(AgentDefinitionCreator,{sessionId:'owner',seed:view.endsWith('read')?null:seed,viewSeed:view.endsWith('read')?seed:null,agents:[],refresh:async()=>{window.refreshes++}}))
`
await browserScenario(async({server,browser})=>{
 for(const view of ['agent-new','agent-edit','agent-read','team-new','team-edit','team-read']){
  const page=await browser.newPage(),errors=[],writes=[];page.setDefaultTimeout(10000)
  page.on('pageerror',error=>errors.push(error.message))
  await page.route('**/api/v1/**',route=>{
   const request=route.request(),path=new URL(request.url()).pathname
   if(request.method()!=='GET')writes.push(path)
   if(path==='/api/v1/plugins/skills')return route.fulfill({json:{skills:[],enabled:true}})
   if(path==='/api/v1/personalities')return route.fulfill({json:{profiles:[]}})
   if(path==='/api/v1/external-agents')return route.fulfill({json:{runners:[],platform:'posix',delegation_enabled:true}})
   return route.fulfill({status:500,json:{error:'Unexpected request'}})
  })
  await page.goto(server.resolvedUrls.local[0]+'__agent-authoring-resource?view='+view)
  const dialog=page.getByRole('dialog'),team=view.startsWith('team'),readOnly=view.endsWith('read')
  await dialog.waitFor()
  await dialog.locator('details').evaluateAll(nodes=>nodes.forEach(node=>{node.open=true}))
  for(const locale of ['en','zh-CN','en'])for(const dark of [false,true])for(const width of [390,1440]){
   await page.setViewportSize({width,height:900});await page.evaluate(({locale,dark})=>{window.locale(locale);document.documentElement.classList.toggle('dark',dark)},{locale,dark})
   const label=key=>catalogs[locale]['ui.agent.author.'+key]
   const title=readOnly?'details':view.endsWith('edit')?team?'editTeamTitle':'editAgentTitle':team?'newTeam':'newAgent'
   await dialog.getByRole('heading',{name:label(title),exact:true}).waitFor()
   const name=dialog.getByRole('textbox',{name:label(team?'teamName':'friendlyName'),exact:true})
   assert.equal(await name.inputValue(),'Original name 原文');assert.equal(await name.isDisabled(),readOnly)
   assert.equal(await dialog.getByRole('textbox',{name:label('prompt'),exact:true}).inputValue(),'Original instructions 原文')
   for(const key of ['advanced','executionSettings'])await dialog.locator('summary').getByText(label(key),{exact:true}).waitFor()
   assert.equal(await dialog.getByRole('textbox',{name:label(team?'teamIdentifier':'agentIdentifier'),exact:true}).inputValue(),view.endsWith('edit')?'fixture':'fixture-custom')
   if(team){
    await dialog.getByRole('heading',{name:label('dispatchTitle'),exact:true}).waitFor()
    for(const key of ['dispatchSmart','dispatchWorkflow'])await dialog.getByRole('option',{name:label(key),exact:true}).waitFor({state:'attached'})
    for(const key of ['timeoutHint','persistentContextHint','strictAcceptanceHint','externalLeadHelp'])await dialog.getByText(label(key),{exact:true}).waitFor()
    await dialog.getByRole('button',{name:label('setLeader')+' · worker',exact:true}).waitFor()
   }else{
    for(const key of ['preview','previewProfession'])await dialog.getByText(label(key),{exact:true}).waitFor()
    await dialog.getByRole('option',{name:label('modelRuntime'),exact:true}).waitFor({state:'attached'})
   }
   await dialog.getByRole('button',{name:label(readOnly?'copy':view.endsWith('edit')?'editSave':'createOnly'),exact:true}).waitFor()
   assert.deepEqual(writes,[]);assert.equal(await page.evaluate(()=>window.refreshes),0)
  }
  assert.deepEqual(errors,[]);await page.close()
 }
 console.log('Authoring: '+keys.size+' finite resource keys; Agent/team new/edit/read forms en→zh→en, both themes and widths, no saves')
},{entry:'/__agent-authoring-resource-entry.js',vite:{plugins:[{name:'agent-authoring-resource',resolveId(id){if(id==='/__agent-authoring-resource-entry.js')return id},load(id){if(id==='/__agent-authoring-resource-entry.js')return entry},configureServer(server){server.middlewares.use('/__agent-authoring-resource',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__agent-authoring-resource','<html><body><div id="root"></div><script type="module" src="/__agent-authoring-resource-entry.js"></script></body></html>'))}catch(error){next(error)}})}}]}})
