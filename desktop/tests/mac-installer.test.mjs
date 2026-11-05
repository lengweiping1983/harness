import assert from 'node:assert/strict'
import {test} from 'node:test'
import {createHash} from 'node:crypto'
import {mkdtemp, readFile, writeFile, mkdir, rm, cp, lstat} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {EventEmitter} from 'node:events'
import {execFile} from 'node:child_process'
import {promisify} from 'node:util'
import {MacInstaller, downloadInstaller, installScript} from '../mac-installer.mjs'
import {sealDevelopmentApp, verifyMacBundle} from '../mac-signing.mjs'
const execute = promisify(execFile)
const bytes = Buffer.from('test installer bytes')
const asset = {browser_download_url:'https://github.com/test/release.dmg',size:bytes.length,digest:'sha256:'+createHash('sha256').update(bytes).digest('hex')}
async function temporary(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'harness-mac-update-test-'))
  t.after(() => rm(root, {recursive:true, force:true}))
  return root
}

test('installer download verifies actual bytes, rejects missing digest, truncation, excess bytes and corrupt digest', async t => {
  const root = await temporary(t)
  const progress = []
  const file = await downloadInstaller({fetch:async () => new Response(bytes),asset,directory:root,signal:new AbortController().signal,progress:value => progress.push(value)})
  assert.deepEqual(await readFile(file), bytes)
  assert.deepEqual(progress,[null,99.9,100])
  for (const [index, payload] of [bytes.subarray(1),Buffer.concat([bytes,bytes]),Buffer.alloc(bytes.length)].entries()) {
    const directory = path.join(root,String(index)); await mkdir(directory)
    await assert.rejects(downloadInstaller({fetch:async () => new Response(payload),asset,directory,progress:()=>{}}), error => error.menuKey === 'updateIntegrityFailed')
  }
  await assert.rejects(downloadInstaller({fetch:()=>assert.fail('must not fetch'),asset:{...asset,digest:null},directory:root,progress:()=>{}}),error=>error.menuKey==='updateIntegrityFailed')
})

test('download honors cancellation and propagates original transport failure without publishing a file', async t => {
  const root = await temporary(t), original = Error('connection dropped')
  await assert.rejects(downloadInstaller({fetch:async()=>{throw original},asset,directory:root,progress:()=>{}}),error=>error===original)
  const controller = new AbortController(); controller.abort()
  await assert.rejects(downloadInstaller({fetch:async(_url,{signal})=>{signal.throwIfAborted()},asset,directory:root,signal:controller.signal,progress:()=>{}}),error=>error.name==='AbortError')
})

async function preparedFixture(t, options = {}) {
  const root = await temporary(t), target = path.join(root,'Harness.app'), calls=[]
  await mkdir(target)
  const command = async (file,args) => {
    calls.push([file,args])
    if (options.fail?.(file,args)) throw Error('native failure')
    if (file.endsWith('hdiutil') && args[0]==='attach') {
      const seal=path.join(args[args.indexOf('-mountpoint')+1],'Harness.app/Contents/_CodeSignature')
      await mkdir(seal,{recursive:true});if (!options.unsealedSource) await writeFile(path.join(seal,'CodeResources'),'fixture seal')
    }
    if (file.endsWith('PlistBuddy')) return {stdout:args[1].includes('CFBundleIdentifier')?'test.harness':args[1].includes('CFBundleExecutable')?'Harness':'1.2.3',stderr:''}
    if (file.endsWith('codesign') && args[0]==='-d') return {stdout:'',stderr:options.adhoc?'# designated => cdhash H"old"\nSignature=adhoc\n':'designated => identifier "test.harness"\n'}
    if (file.endsWith('ditto')) await cp(args[0],args[1],{recursive:true})
    return {stdout:'',stderr:''}
  }
  const installer=new MacInstaller({appPath:target,cachePath:path.join(root,'cache'),fetch:async()=>new Response(bytes),command,...options})
  return {installer,root,target,calls}
}

test('mac preparation validates identity, version, architecture and signature before staging and unmounts before ready',async t=>{
  const {installer,calls,target}=await preparedFixture(t)
  const ready=await installer.prepare(asset,'1.2.3',()=>{})
  assert.equal(ready.target,target)
  assert.equal(calls.at(-1)[0],'/usr/bin/hdiutil')
  assert.equal(calls.at(-1)[1][0],'detach')
  assert.equal(calls.filter(([file,args])=>file.endsWith('codesign')&&args.includes('-R')).length,2)
  assert.ok(calls.some(([file,args])=>file.endsWith('lipo')&&args.includes('-verify_arch')))
  await installer.discard()
  await assert.rejects(lstat(ready.staged),{code:'ENOENT'})
  assert.ok(await lstat(target))
})

test('native verification or detach failure cannot publish a prepared installation and preserves current application',async t=>{
  for(const boundary of ['codesign','lipo','detach']) {
    const {installer,calls,target}=await preparedFixture(t,{fail:(file,args)=>file.endsWith(boundary)||args[0]===boundary})
    await assert.rejects(installer.prepare(asset,'1.2.3',()=>{}))
    assert.equal(installer.prepared,null)
    assert.ok(await lstat(target))
    assert.ok(calls.some(([file,args])=>file.endsWith('hdiutil')&&args[0]==='detach'))
  }
})

test('ad hoc installations still verify both bundles without pinning the old code hash',async t=>{
  const {installer,calls}=await preparedFixture(t,{adhoc:true})
  await installer.prepare(asset,'1.2.3',()=>{})
  assert.equal(calls.filter(([file,args])=>file.endsWith('codesign')&&args[0]==='--verify').length,3)
  assert.equal(calls.filter(([,args])=>args.includes('-R')).length,0)
})

test('installation handoff is single-use and passes paths and translated failure as arguments',async t=>{
  const launches=[]
  const {installer}=await preparedFixture(t,{launch:(file,args,options)=>{
    launches.push({file,args,options});const child=new EventEmitter();child.unref=()=>{};queueMicrotask(()=>child.emit('spawn'));return child
  }})
  await installer.prepare(asset,'1.2.3',()=>{})
  const first=installer.install('安装失败 $() " quoted')
  assert.equal(installer.install('other'),first)
  await first
  assert.equal(launches.length,1)
  assert.equal(launches[0].file,'/bin/bash')
  assert.ok(launches[0].args[6].startsWith('安装失败 $() " quoted'))
  assert.equal(launches[0].options.detached,true)
})

test('mac install helper replaces isolated application, keeps backup, waits for exit, rejects changed target or signature and rolls back failed replacement', {skip:process.platform!=='darwin'},async t=>{
  const root=await temporary(t)
  for (const mode of ['success','changed','replacement-failed','signature-failed','parent-running']) {
    const dir=path.join(root,mode);await mkdir(dir)
    const target=path.join(dir,'Harness.app'),staged=path.join(dir,'staged.app'),backup=path.join(dir,'previous.app')
    await mkdir(target);await mkdir(staged)
    await writeFile(path.join(target,'version'),'old');await writeFile(path.join(staged,'version'),'new')
    const stat=await lstat(target)
    // 仅替换系统 UI 出口；实际 bash、stat、mv 和回滚均操作隔离目录。
    let script=installScript.replace('/usr/bin/open "$target"','/usr/bin/true').replace('/usr/bin/osascript','/usr/bin/false').replaceAll('/usr/bin/codesign','/usr/bin/true')
    if(mode==='signature-failed') script=script.replaceAll('/usr/bin/true --verify','/usr/bin/false --verify')
    if(mode==='parent-running') script=script.replace('i<120','i<1').replace('/bin/sleep 1','/usr/bin/true')
    if(mode==='replacement-failed') script=script.replace('/bin/mv "$staged" "$target"','/usr/bin/false')
    const file=path.join(dir,'install.sh');await writeFile(file,script)
    const action=execute('/bin/bash',[file,mode==='parent-running'?String(process.pid):'99999999',target,staged,backup,dir,'failure',mode==='changed'?'0:0':`${stat.dev}:${stat.ino}`,''])
    if(mode==='success') {
      await action
      assert.equal(await readFile(path.join(target,'version'),'utf8'),'new')
      assert.equal(await readFile(path.join(backup,'version'),'utf8'),'old')
      assert.equal(await readFile(path.join(dir,'result'),'utf8'),'installed\n')
    } else {
      await assert.rejects(action)
      assert.equal(await readFile(path.join(target,'version'),'utf8'),'old')
      await assert.rejects(lstat(path.join(dir,'result')),{code:'ENOENT'})
    }
  }
})

test('native macOS DMG preparation mounts, checks actual ad hoc signatures, stages and detaches an isolated app', {skip:process.platform!=='darwin'},async t=>{
  const root=await temporary(t),source=path.join(root,'source'),bundle=path.join(source,'Harness.app')
  await mkdir(path.join(bundle,'Contents/MacOS'),{recursive:true})
  await writeFile(path.join(root,'main.c'),'int main(void) { return 0; }\n')
  await execute('/usr/bin/clang',[path.join(root,'main.c'),'-o',path.join(bundle,'Contents/MacOS/Harness')])
  await writeFile(path.join(bundle,'Contents/Info.plist'),`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>CFBundleIdentifier</key><string>test.harness.update</string><key>CFBundleExecutable</key><string>Harness</string><key>CFBundleShortVersionString</key><string>1.2.3</string><key>CFBundleVersion</key><string>1.2.3</string><key>CFBundlePackageType</key><string>APPL</string></dict></plist>`)
  const unsealed=path.join(root,'Unsealed.app');await cp(bundle,unsealed,{recursive:true})
  await assert.rejects(execute('/usr/bin/codesign',['--verify','--deep','--strict',unsealed]))
  const context={electronPlatformName:'darwin',appOutDir:source,packager:{config:{},appInfo:{id:'test.harness.update',productFilename:'Harness'}}}
  await sealDevelopmentApp(context)
  await verifyMacBundle(context)
  const target=path.join(root,'Harness.app');await cp(bundle,target,{recursive:true})
  const image=path.join(root,'update.dmg')
  await execute('/usr/bin/hdiutil',['create','-srcfolder',source,'-volname','HarnessUpdateTest','-format','UDZO',image],{timeout:120000})
  const payload=await readFile(image)
  const realAsset={...asset,size:payload.length,digest:'sha256:'+createHash('sha256').update(payload).digest('hex')}
  const installer=new MacInstaller({appPath:target,cachePath:path.join(root,'cache'),fetch:async()=>new Response(payload)})
  const ready=await installer.prepare(realAsset,'1.2.3',()=>{})
  await execute('/usr/bin/codesign',['--verify','--deep','--strict',ready.staged])
  assert.ok(await lstat(path.join(ready.staged,'Contents/MacOS/Harness')))
  const mounts=await execute('/usr/bin/hdiutil',['info'])
  assert.ok(!mounts.stdout.includes(path.join(ready.workspace,'volume')))
  await installer.discard()
  const recovery=new MacInstaller({appPath:unsealed,cachePath:path.join(root,'cache'),fetch:async()=>new Response(payload)})
  const recovered=await recovery.prepare(realAsset,'1.2.3',()=>{})
  await execute('/usr/bin/codesign',['--verify','--deep','--strict',recovered.staged])
  // 恢复只准备新包，不能给当前安装原件修签。
  await assert.rejects(execute('/usr/bin/codesign',['--verify','--deep','--strict',unsealed]))
  await recovery.discard()
})

test('wrong bundle version detaches the image and combined verification and detach failures preserve both causes',async t=>{
  const mismatch=await preparedFixture(t)
  await assert.rejects(mismatch.installer.prepare(asset,'2.0.0',()=>{}),error=>error.menuKey==='updateInvalidRelease')
  assert.equal(mismatch.installer.prepared,null)
  assert.equal(mismatch.calls.at(-1)[1][0],'detach')
  const combined=await preparedFixture(t,{fail:(file,args)=>file.endsWith('codesign')||args[0]==='detach'})
  await assert.rejects(combined.installer.prepare(asset,'1.2.3',()=>{}),error=>error instanceof AggregateError&&error.errors.length===2)
})

test('failed helper launch is retained as a single failed handoff and never automatically replayed',async t=>{
  let launches=0
  const original=Error('spawn denied')
  const {installer}=await preparedFixture(t,{launch:()=>{
    launches++;const child=new EventEmitter();queueMicrotask(()=>child.emit('error',original));return child
  }})
  await installer.prepare(asset,'1.2.3',()=>{})
  const first=installer.install('failure')
  await assert.rejects(first,error=>error===original)
  assert.equal(installer.install('retry'),first)
  assert.equal(launches,1)
})

test('damaged sealed current app reports reinstall guidance and an invalid new seal reports integrity failure',async t=>{
  for(const mode of ['current','new']) {
    const {installer,target}=await preparedFixture(t,{fail:(file,args)=>file.endsWith('codesign')&&args[0]==='--verify'&&(mode==='current'?args.at(-1).endsWith('/Harness.app')&&!args.at(-1).includes('/volume/'):args.at(-1).includes('/volume/'))})
    await assert.rejects(installer.prepare(asset,'1.2.3',()=>{}),error=>error.menuKey===(mode==='current'?'updateCurrentSignatureInvalid':'updateIntegrityFailed')&&error.cause.message==='native failure')
    assert.equal(installer.prepared,null)
    assert.ok(await lstat(target))
  }
})

test('an incoming app without a resource seal is rejected even if a native signature command would succeed',async t=>{
  const {installer,calls}=await preparedFixture(t,{unsealedSource:true})
  await assert.rejects(installer.prepare(asset,'1.2.3',()=>{}),error=>error.menuKey==='updateIntegrityFailed'&&error.cause.code==='ENOENT')
  assert.equal(installer.prepared,null)
  assert.equal(calls.some(([file])=>file.endsWith('ditto')),false)
  assert.equal(calls.at(-1)[1][0],'detach')
})
