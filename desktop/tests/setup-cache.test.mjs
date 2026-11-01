import assert from 'node:assert/strict'
import {test} from 'node:test'
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import path from 'node:path'
import os from 'node:os'
import {prepareDesktop} from '../setup.mjs'
import {developmentPython} from '../python-runtime.mjs'
const python=developmentPython({root:fileURLToPath(new URL('../../',import.meta.url))})

test('changing the UI build gate invalidates the prepared interface cache',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'harness-setup-cache-'))
 t.after(()=>rm(root,{recursive:true,force:true}))
 for(const file of ['.run/desktop-python/'+(process.platform==='win32'?'Scripts/python.exe':'bin/python'),'web/node_modules/typescript/package.json','desktop/node_modules/electron/package.json','web/dist/index.html','scripts/check_ui_rules.mjs']){
  const target=path.join(root,file);await mkdir(path.dirname(target),{recursive:true});await writeFile(target,'fixture')
 }
 const notices=[]
 const builds=[],installs=[],catalogs=[]
 const run=async(command,args,options)=>{
  if(args.includes('import sys; print(sys.executable)'))return python
  if(args.includes('pip'))installs.push(args)
  if(args.includes('scripts/check_i18n.mjs'))catalogs.push(args)
  if(args.includes('build'))builds.push({command,args,options})
  return ''
 }
 const options={root,env:process.env,run,notify:text=>notices.push(text),nodeVersion:'22.23.2',language:'zh-CN'}
 await prepareDesktop(options)
 assert.equal(builds.length,1)
 assert.ok(notices.some(text=>text.includes('正在构建桌面界面')))
 assert.ok(notices.some(text=>text.includes('Harness Desktop 已就绪')))
 options.language='en'
 await prepareDesktop(options)
 assert.ok(notices.includes('Harness Desktop is ready.'))
 assert.equal(builds.length,1)
 await writeFile(path.join(root,'scripts/check_ui_rules.mjs'),'changed build rules')
 await prepareDesktop(options)
 assert.equal(builds.length,2)
 await mkdir(path.join(root,'harness/i18n/locales'),{recursive:true})
 await writeFile(path.join(root,'harness/i18n/locales/en.json'),'changed authoritative catalog')
 await prepareDesktop(options)
 assert.equal(builds.length,3)
 await writeFile(path.join(root,'scripts/ui_css_rules.mjs'),'changed CSS declaration checks')
 await prepareDesktop(options)
 assert.equal(builds.length,4)
 await writeFile(path.join(root,'scripts/ui_class_rules.mjs'),'changed class rules')
 await prepareDesktop(options)
 assert.equal(builds.length,5)
 const asset=path.join(root,'web/dist/fixture.js')
 await writeFile(asset,'original')
 await prepareDesktop(options)
 const beforeArtifact=builds.length
 await writeFile(asset,'changed')
 await prepareDesktop(options)
 assert.equal(builds.length,beforeArtifact+1)
 await rm(asset)
 await prepareDesktop(options)
 assert.equal(builds.length,beforeArtifact+2)
 await mkdir(path.join(root,'web/public'),{recursive:true})
 await writeFile(path.join(root,'web/public/fixture.txt'),'new public input')
 await prepareDesktop(options)
 assert.equal(builds.length,beforeArtifact+3)
 const beforeInstall=installs.length
 for(const file of ['build_support.py','MANIFEST.in','README.md']){
  await writeFile(path.join(root,file),'changed '+file)
  await prepareDesktop(options)
 }
 assert.equal(installs.length,beforeInstall+3)
 const beforeCatalog=catalogs.length
 await mkdir(path.join(root,'web/src'),{recursive:true})
 await writeFile(path.join(root,'web/src/fixture.ts'),'changed explicit resource key')
 await prepareDesktop(options)
 assert.equal(catalogs.length,beforeCatalog+1)
 const beforeResearch=builds.length
 await mkdir(path.join(root,'web/src/research'),{recursive:true})
 await writeFile(path.join(root,'web/src/research/fixture.ts'),'excluded fixture')
 await prepareDesktop(options)
 assert.equal(builds.length,beforeResearch)
 assert.equal(catalogs.length,beforeCatalog+1)
 const saved=await readFile(path.join(root,'.run/desktop-setup.json'),'utf8')
 for(const [language,message] of [['en','The desktop interface build is incomplete. Check the build output before starting again.'],['zh-CN','桌面界面构建不完整，请检查构建输出后再启动。']]){
  options.language=language
  await rm(path.join(root,'web/dist/index.html'),{force:true})
  await assert.rejects(prepareDesktop(options),error=>error.message===message)
  assert.equal(await readFile(path.join(root,'.run/desktop-setup.json'),'utf8'),saved)
 }
})

test('corrupt setup state is preserved and stops before installation or build',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'harness-setup-invalid-'))
 t.after(()=>rm(root,{recursive:true,force:true}))
 const state=path.join(root,'.run/desktop-setup.json')
 await mkdir(path.dirname(state),{recursive:true})
 for(const language of ['en','zh-CN','en'])for(const original of [Buffer.from('{broken json'),Buffer.from([0xff,0xfe,0x41]),...['null','[]','true','{"unknown":"x"}','{"build":123}','{"build":"invalid"}'].map(value=>Buffer.from(value))]){
  await writeFile(state,original)
  const operations=[]
  const run=async(command,args)=>{
   if(args.includes('import sys; print(sys.executable)'))return python
   operations.push(args)
   return ''
  }
  await assert.rejects(prepareDesktop({root,env:process.env,language,run,notify:()=>{},nodeVersion:'22.23.2'}),error=>{
   assert.equal(error.code,'desktop/setup-state-invalid')
   assert.equal(/[\u4e00-\u9fff]/.test(error.message),language==='zh-CN')
   assert.ok(error.message.includes(state))
   return true
  })
  assert.deepEqual(await readFile(state),original)
  assert.ok(operations.every(args=>args.includes('import sys; sys.exit(0 if sys.version_info[:2] >= (3, 14) else 1)')))
 }
})

test('explicit Python is validated even when an isolated runtime already exists',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'harness-explicit-python-'))
 t.after(()=>rm(root,{recursive:true,force:true}))
 const isolated=path.join(root,'.run/desktop-python',process.platform==='win32'?'Scripts/python.exe':'bin/python')
 await mkdir(path.dirname(isolated),{recursive:true});await writeFile(isolated,'existing runtime')
 const calls=[]
 const explicit=path.join('chosen','python')
 const selected=path.resolve(root,explicit)
 const run=async(command,args)=>{
  calls.push(command)
  throw Error('invalid explicitly selected interpreter')
 }
 await assert.rejects(prepareDesktop({root,env:{...process.env,HARNESS_PYTHON:explicit},run,notify:()=>{},nodeVersion:'22.23.2'}))
 assert.deepEqual(calls,[selected])
 assert.equal(await readFile(isolated,'utf8'),'existing runtime')
})

test('preparation prerequisites render current language before any install',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'harness-setup-language-'))
 t.after(()=>rm(root,{recursive:true,force:true}))
 for(const language of ['en','zh-CN','en']){
  for(const nodeVersion of ['21.12.0','22.23.2']){
   let calls=0
   const run=async()=>{calls++;throw Error('Original probe diagnostic')}
   await assert.rejects(prepareDesktop({root,env:{...process.env,HARNESS_PYTHON:'explicit-fixture'},language,nodeVersion,run,notify:()=>{}}),error=>{
    assert.equal(/[\u4e00-\u9fff]/.test(error.message),language==='zh-CN')
    assert.ok(error.message.includes(nodeVersion==='21.12.0'?'Node.js':'Python'))
    return true
   })
   assert.equal(calls,nodeVersion==='21.12.0'?0:1)
  }
 }
})

test('Electron preparation failure keeps resource language and original diagnostic cause',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'harness-electron-failure-'))
 t.after(()=>rm(root,{recursive:true,force:true}))
 for(const language of ['en','zh-CN','en']){
  const failure=new Error('Original {error} diagnostic')
  const calls=[],notices=[]
  const run=async(command,args)=>{
   calls.push(args)
   if(args.includes('import sys; print(sys.executable)'))return python
   if(args.includes('node_modules/electron/install.js'))throw failure
   return ''
  }
  await assert.rejects(prepareDesktop({root,language,env:process.env,run,notify:text=>notices.push(text),nodeVersion:'22.23.2'}),error=>{
   assert.equal(error.cause,failure)
   assert.ok(error.message.includes(failure.message))
   assert.equal(/[\u4e00-\u9fff]/.test(error.message),language==='zh-CN')
   assert.ok(error.message.includes('ELECTRON_MIRROR'))
   return true
  })
  assert.equal(calls.filter(args=>args.includes('node_modules/electron/install.js')).length,1)
  assert.equal(calls.filter(args=>args.includes('build')).length,0)
  assert.ok(!notices.includes('Harness Desktop is ready.')&&!notices.includes('Harness Desktop 已就绪。'))
 }
})
