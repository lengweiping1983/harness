import assert from 'node:assert/strict'
import {test} from 'node:test'
import {mkdtemp,rm,readFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {createHash} from 'node:crypto'
import {downloadInstaller} from '../mac-installer.mjs'
import {InstallerUpdates} from '../installer-updates.mjs'
import {applicationMenu,refreshUpdateMenu,updateDockProgress} from '../application-menu.mjs'
import {setupCopy} from '../setup-copy.mjs'
const bytes=Buffer.alloc(1024*1024,7)
const asset={size:bytes.length,digest:'sha256:'+createHash('sha256').update(bytes).digest('hex'),browser_download_url:'https://github.com/owner/repo/update.dmg'}
async function temporary(t){const directory=await mkdtemp(path.join(tmpdir(),'harness-progress-'));t.after(()=>rm(directory,{recursive:true,force:true}));return directory}

test('download publishes sub-percent bytes before EOF, then verification without reporting installation ready',async t=>{
  const directory=await temporary(t),events=[];let stream,first
  const progressed=new Promise(resolve=>{first=resolve})
  const body=new ReadableStream({start(controller){stream=controller;controller.enqueue(bytes.subarray(0,1024))}})
  const action=downloadInstaller({fetch:async()=>new Response(body),asset,directory,progress:(percent,details)=>{events.push({percent,...details});if(details.received===1024)first()}})
  await progressed
  assert.equal(events[0].phase,'connecting');assert.equal(events[0].percent,null)
  assert.equal(events.at(-1).phase,'downloading');assert.equal(events.at(-1).received,1024)
  assert.ok(events.at(-1).percent>0&&events.at(-1).percent<1)
  stream.enqueue(bytes.subarray(1024));stream.close()
  await action
  assert.equal(events.at(-1).phase,'verifying')
  assert.deepEqual(await readFile(path.join(directory,'installer.dmg')),bytes)
})

test('no-response and stalled-body transfers abort once with a timeout and never claim completion',async t=>{
  for(const stage of ['headers','body']) {
    const directory=path.join(await temporary(t),stage);const {mkdir}=await import('node:fs/promises');await mkdir(directory)
    let signal;const events=[]
    const fetch=async(_url,options)=>{
      signal=options.signal
      if(stage==='headers')return new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true}))
      return new Response(new ReadableStream({start(controller){controller.enqueue(bytes.subarray(0,1024));signal.addEventListener('abort',()=>controller.error(signal.reason),{once:true})}}))
    }
    await assert.rejects(downloadInstaller({fetch,asset,directory,idleTimeoutMs:25,progress:(_percent,details)=>events.push(details)}),error=>error.name==='TimeoutError')
    assert.equal(signal.aborted,true);assert.ok(!events.some(event=>event.phase==='verifying'))
  }
})

test('installer keeps connecting, byte transfer and preparing distinct until the original preparation resolves',async()=>{
  let report,finish;const notified=[]
  const installer={prepare:async(_asset,_version,progress)=>{report=progress;await new Promise(resolve=>{finish=resolve})}}
  const updates=new InstallerUpdates({source:{provider:'github',owner:'owner',repo:'repo'},version:'1.0.0',packaged:true,installer,notify:()=>notified.push(updates.snapshot())})
  updates.asset=asset;updates.version='1.1.0';updates.state='available'
  const pending=updates.download();assert.equal(updates.phase,'connecting');assert.equal(updates.progress,null)
  await Promise.resolve()
  report(0.1,{phase:'downloading',received:1024,total:asset.size})
  assert.equal(updates.received,1024)
  report(100,{phase:'preparing',received:asset.size,total:asset.size})
  assert.equal(updates.state,'downloading');assert.equal(updates.phase,'preparing')
  finish();await pending
  assert.equal(updates.state,'downloaded');assert.equal(updates.phase,'ready')
  assert.deepEqual(notified.map(item=>item.phase),['connecting','downloading','preparing','ready'])
})

test('one menu item updates in place across English Chinese English, showing bytes and phase instead of misleading zero',()=>{
  let commandCount=0
  const item={label:'',enabled:true},menu={getMenuItemById:id=>{assert.equal(id,'harness-updates');return item}}
  for(const locale of ['en','zh-CN','en']) {
    const copy=setupCopy(locale),text=key=>copy('ui.desktop.menu.'+key)
    for(const phase of ['connecting','verifying','preparing']) {
      refreshUpdateMenu(menu,{state:'downloading',phase,progress:0},text)
      assert.equal(item.label,text({connecting:'updateConnectingMenu',verifying:'updateVerifyingMenu',preparing:'updatePreparingMenu'}[phase]))
      assert.ok(!item.label.includes('0%'));assert.equal(item.enabled,false)
      assert.equal(updateDockProgress({state:'downloading',phase,progress:0}),2)
    }
    const status={state:'downloading',phase:'downloading',progress:0.09765625,received:1024,total:1024*1024}
    refreshUpdateMenu(menu,status,text)
    assert.ok(item.label.includes('0.1%'));assert.ok(item.label.includes('1.0 KB'));assert.ok(item.label.includes('1.0 MB'))
    assert.equal(updateDockProgress(status),status.progress/100)
    const template=applicationMenu({platform:'darwin',text,command:()=>{commandCount++},updateState:status.state,updatePhase:status.phase,updateProgress:status.progress,updateReceived:status.received,updateTotal:status.total})
    assert.equal(template.find(entry=>entry.label===text('help')).submenu[0].label,item.label)
    refreshUpdateMenu(menu,{state:'downloaded',phase:'ready',progress:100},text)
    assert.equal(item.label,text('updateReadyMenu'));assert.equal(item.enabled,true)
  }
  assert.equal(commandCount,0)
})

test('real Electron streamed response advances the same native menu before the server finishes the installer',async()=>{
  const {execFile}=await import('node:child_process'),{promisify}=await import('node:util'),{createRequire}=await import('node:module'),{fileURLToPath}=await import('node:url')
  const profile=await mkdtemp(path.join(tmpdir(),'harness-native-progress-'))
  const env={...process.env};delete env.ELECTRON_RUN_AS_NODE
  try {
    const result=await promisify(execFile)(createRequire(import.meta.url)('electron'),[fileURLToPath(new URL('./fixtures/update-progress-app.mjs',import.meta.url)),profile],{env,timeout:15000,maxBuffer:1024*1024})
    assert.match(result.stdout,/native-streaming-update-progress-passed/)
  } finally {await rm(profile,{recursive:true,force:true})}
})

test('receiving new bytes resets the idle deadline instead of imposing a total download timeout',async t=>{
  const directory=await temporary(t)
  t.mock.timers.enable({apis:['setTimeout']})
  let stream,signal,next
  let received=new Promise(resolve=>{next=resolve})
  const payload=Buffer.from([1,2,3])
  const small={...asset,size:3,digest:'sha256:'+createHash('sha256').update(payload).digest('hex')}
  const body=new ReadableStream({start(controller){stream=controller;controller.enqueue(payload.subarray(0,1))}})
  const downloading=downloadInstaller({fetch:async(_url,options)=>{signal=options.signal;return new Response(body)},asset:small,directory,idleTimeoutMs:1000,progress:(_value,details)=>{if(details.phase==='downloading')next()}})
  await received
  for(let index=1;index<3;index++) {
    t.mock.timers.tick(900)
    assert.equal(signal.aborted,false)
    received=new Promise(resolve=>{next=resolve})
    stream.enqueue(payload.subarray(index,index+1))
    await received
  }
  stream.close();await downloading
  assert.equal(signal.aborted,false)
  t.mock.timers.tick(30000)
  assert.equal(signal.aborted,false)
})
