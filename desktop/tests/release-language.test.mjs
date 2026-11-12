import assert from 'node:assert/strict'
import {test} from 'node:test'
import {verifyRuntime} from '../verify-runtime.mjs'
import {setupCopy} from '../setup-copy.mjs'

test('bundled runtime validation uses each call language and retains machine identities',()=>{
  for(const language of ['en','zh-CN','en']) {
    const copy=setupCopy(language),options={platform:'darwin',arch:'arm64',version:'fixture',language}
    // 空技能清单合法：无预装技能时不报错。
    assert.equal(verifyRuntime({bundledSkills:[],...options},options),undefined)
    // 非数组或含空白名称的无效清单仍被拒绝。
    assert.throws(()=>verifyRuntime({bundledSkills:null,...options},options),error=>
      error.message===copy('desktop.release.skillsMissing'))
    assert.throws(()=>verifyRuntime({bundledSkills:['  '],...options},options),error=>
      error.message===copy('desktop.release.skillsMissing'))
    assert.throws(()=>verifyRuntime({bundledSkills:['skill'],platform:'win32'},options),error=>
      error.message===copy('desktop.release.bundleMismatch',options))
    assert.equal(verifyRuntime({bundledSkills:['skill'],...options},options),undefined)
  }
})

import {mkdtemp,mkdir,writeFile,readFile,rm,copyFile,cp} from 'node:fs/promises'
import {existsSync} from 'node:fs'
import {createHash} from 'node:crypto'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {prepareRelease} from '../prepare-release.mjs'
import beforePack from '../verify-runtime.mjs'
import {Arch} from 'builder-util'

async function expectedVoice(root,platform,arch) {
  const tag=`${platform==='darwin'?'macos':'windows'}-${arch==='x64'?'x86_64':arch}`
  const relative=`harness/resources/hex/${tag}/${platform==='win32'?'hex.exe':'hex'}`
  const manifest=JSON.parse(await readFile(path.join(root,path.dirname(relative),'engine.json'),'utf8'))
  return {path:relative,sha256:createHash('sha256').update(await readFile(path.join(root,relative))).digest('hex'),platform,arch,version:manifest.version,source:manifest.source}
}

async function fixture(t,{electron=true,count=1,identity={},failure,failureAt=()=>true}={}) {
  const root=await mkdtemp(path.join(tmpdir(),'harness-release-原文 空格-{path}-'))
  t.after(()=>rm(root,{recursive:true,force:true}))
  const staging=path.join(root,'.run/desktop-release/resources')
  await mkdir(path.join(root,'desktop'),{recursive:true})
  await writeFile(path.join(root,'desktop/package.json'),JSON.stringify({version:'fixture'}))
  await copyFile(new URL('../python-downloads.json',import.meta.url),path.join(root,'desktop/python-downloads.json'))
  for (const name of ['voice-native.swift','voice-native.ps1']) await copyFile(new URL('../'+name,import.meta.url),path.join(root,'desktop',name))
  for (const [platform,arch] of [['darwin','arm64'],['darwin','x64'],['win32','x64']]) {
    const tag=`${platform==='darwin'?'macos':'windows'}-${arch==='x64'?'x86_64':arch}`
    const file=path.join(root,'harness/resources/hex',tag,platform==='win32'?'hex.exe':'hex')
    await mkdir(path.dirname(file),{recursive:true})
    const header=Buffer.alloc(128)
    if (platform==='darwin') {header.writeUInt32LE(0xfeedfacf,0);header.writeUInt32LE(arch==='arm64'?0x100000c:0x1000007,4)}
    else {header.write('MZ',0);header.writeUInt32LE(64,60);header.writeUInt32LE(0x4550,64);header.writeUInt16LE(0x8664,68)}
    await writeFile(file,header)
    await writeFile(path.join(path.dirname(file),'engine.json'),JSON.stringify({version:'3.3.0',source:'fixture',sha256:createHash('sha256').update(header).digest('hex')}))
    await writeFile(path.join(path.dirname(file),'LICENSE'),'Vendored license 原文')
    await writeFile(path.join(path.dirname(file),'THIRD_PARTY_NOTICES.md'),'Vendored notices 原文')
  }

  await mkdir(path.join(root,'web/dist'),{recursive:true})
  await writeFile(path.join(root,'web/dist/index.html'),'Original HTML')
  await writeFile(path.join(root,'web/dist/voice.html'),'Original voice HTML')
  await mkdir(path.join(root,'.harness/skills'),{recursive:true})
  await writeFile(path.join(root,'.harness/skills/original.md'),'Original skill 原文')
  const calls=[],logs=[]
  const execute=async(command,args,options)=>{
    calls.push({command,args,options})
    if(failure&&failureAt(command,args))throw failure
    if(command==='/usr/bin/xcrun') {
      await writeFile(args.at(-1),'fixture native helper')
    } else if(args[0].endsWith('install.js')) {
      if(electron)await mkdir(path.join(root,'desktop/node_modules/electron/dist'),{recursive:true})
    } else if(command==='uv'&&args[0]==='python') {
      const directory=args[args.indexOf('--install-dir')+1]
      await mkdir(directory,{recursive:true})
      for(let index=0;index<count;index++) {
        const runtime=path.join(directory,'cpython-3.14.10-fixture-'+index)
        await mkdir(path.join(runtime,'bin'),{recursive:true})
        await writeFile(path.join(runtime,'bin/python3'),'fixture runtime')
        await writeFile(path.join(runtime,'python.exe'),'fixture runtime')
      }
    } else if(command==='uv') {
      const site=args[args.indexOf('--target')+1]
      await mkdir(path.join(site,'harness'),{recursive:true})
      await writeFile(path.join(site,'harness/cli.py'),'# fixture')
      if(args.includes('--no-deps')) {
        const tag=identity.platform==='win32'?'windows-x86_64':identity.machine==='x86_64'?'macos-x86_64':'macos-arm64'
        await cp(path.join(root,'harness/resources/hex',tag),path.join(site,'harness/resources/hex',tag),{recursive:true})
      }
    } else if(args[1].includes('platform.machine')) {
      return JSON.stringify({platform:'darwin',machine:'arm64',version:'3.14.10',...identity})
    } else if(args[1].includes('build_default_registry'))return JSON.stringify(['fixture-skill'])
    return ''
  }
  return {root,staging,calls,logs,options:{root,platform:'darwin',arch:'arm64',execute,log:value=>logs.push(value)}}
}

test('release preparation rejects before later effects and preserves original staging',async t=>{
  for(const language of ['en','zh-CN','en']) {
    const copy=setupCopy(language)
    for(const reason of ['platform','staging','electron','runtimeCount','runtimePlatform','runtimeArch','runtimeVersion']) {
      await t.test(`${language}: ${reason}`,async t=>{
        const data=await fixture(t,{
          electron:reason!=='electron',count:reason==='runtimeCount'?2:1,
          identity:reason==='runtimePlatform'?{platform:'win32'}:reason==='runtimeArch'?{machine:'x86_64'}:reason==='runtimeVersion'?{version:'0.0.0'}:{},
        })
        if(reason==='staging') {
          await mkdir(path.join(data.staging,'site'),{recursive:true})
          await writeFile(path.join(data.staging,'site/original'),'Original bytes 原文')
        }
        const key={platform:'platformUnsupported',staging:'stagingNotFresh',electron:'electronMissing',runtimeCount:'runtimeCount'}[reason]??'runtimeMismatch'
        const params={path:reason==='electron'?path.join(data.root,'desktop/node_modules/electron/dist'):data.staging}
        await assert.rejects(prepareRelease({...data.options,language,...(reason==='platform'?{platform:'linux'}:{})}),error=>
          error.message===copy('desktop.release.'+key,params))
        const expectedCalls={platform:0,staging:0,electron:4,runtimeCount:2}[reason]??3
        assert.equal(data.calls.length,expectedCalls)
        for(const call of data.calls)assert.equal(call.options.language,language)
        assert.deepEqual(data.logs,[])
        if(reason==='staging')assert.equal(await readFile(path.join(data.staging,'site/original'),'utf8'),'Original bytes 原文')
      })
    }
  }
})

test('release preparation stages current files and verifies metadata without executing installers',async t=>{
  for(const language of ['en','zh-CN','en'])for(const [platform,arch,machine] of [['darwin','arm64','arm64'],['darwin','x64','x86_64'],['win32','x64','AMD64']]) {
    await t.test(`${language}: ${platform}/${arch}`,async t=>{
      const data=await fixture(t,{identity:{platform,machine}})
      await prepareRelease({...data.options,platform,arch,language})
      const offset=platform==='darwin'?1:0
      assert.equal(data.calls.length,8+offset)
      for(const call of data.calls)assert.equal(call.options.language,language)
      const bootstrap=data.calls[offset]
      assert.equal(bootstrap.command,'uv')
      assert.deepEqual(bootstrap.args.slice(0,3),['python','install','3.14.10'])
      assert.ok(bootstrap.args.includes('--no-bin'))
      assert.ok(bootstrap.args.includes('--no-registry'))
      const catalog=fileURLToPath(bootstrap.args[bootstrap.args.indexOf('--python-downloads-json-url')+1])
      assert.equal(catalog,path.join(data.root,'desktop/python-downloads.json'))
      const downloads=JSON.parse(await readFile(catalog,'utf8'))
      const target=`cpython-3.14.10-${platform==='win32'?'windows':platform}-${arch==='x64'?'x86_64':'aarch64'}-none`
      assert.equal(downloads[target].patch,10)
      assert.match(downloads[target].sha256,/^[a-f0-9]{64}$/)
      assert.match(data.calls[offset+1].args[1],/platform.machine/)
      assert.ok(data.calls[offset+2].args[0].endsWith('install.js'))
      assert.ok(data.calls[offset+3].args.includes('--require-hashes'))
      assert.ok(data.calls[offset+4].args.includes('--no-deps'))
      assert.equal(await readFile(path.join(data.staging,'web/index.html'),'utf8'),'Original HTML')
      assert.equal(await readFile(path.join(data.staging,'site/.harness/skills/original.md'),'utf8'),'Original skill 原文')
      assert.deepEqual(JSON.parse(await readFile(path.join(data.staging,'runtime.json'),'utf8')),
        {version:'fixture',platform,arch,python:'3.14.10',bundledSkills:['fixture-skill'],voice:await expectedVoice(data.root,platform,arch)})
      await beforePack({packager:{projectDir:path.join(data.root,'desktop'),appInfo:{version:'fixture'}},electronPlatformName:platform,arch:Arch[arch]})
      assert.deepEqual(data.logs,[setupCopy(language)('desktop.release.staged',{path:data.staging})])
    })
  }
})

test('release preparation stages an explicit voice override without changing repository resources',async t=>{
  for(const language of ['en','zh-CN','en'])for(const [platform,arch,machine] of [['darwin','arm64','arm64'],['win32','x64','AMD64']]) {
    await t.test(`${language}: ${platform}`,async t=>{
      const data=await fixture(t,{identity:{platform,machine}})
      const original=await expectedVoice(data.root,platform,arch)
      const source=path.join(data.root,'custom',platform==='win32'?'hex.exe':'hex')
      const bytes=Buffer.from(await readFile(path.join(data.root,original.path)))
      bytes[100]=42
      await mkdir(path.dirname(source),{recursive:true})
      await writeFile(source,bytes)
      await writeFile(path.join(path.dirname(source),'LICENSE'),'Override license 原文')
      await prepareRelease({...data.options,platform,arch,language,voiceBinary:source})
      assert.deepEqual(await expectedVoice(data.root,platform,arch),original)
      assert.equal(await readFile(path.join(data.root,path.dirname(original.path),'LICENSE'),'utf8'),'Vendored license 原文')
      assert.equal(await readFile(path.join(data.root,path.dirname(original.path),'THIRD_PARTY_NOTICES.md'),'utf8'),'Vendored notices 原文')
      const installed=path.join(data.staging,'site',original.path)
      assert.deepEqual(await readFile(installed),bytes)
      assert.equal(await readFile(path.join(path.dirname(installed),'LICENSE'),'utf8'),'Override license 原文')
      assert.equal(existsSync(path.join(path.dirname(installed),'engine.json')),false)
      assert.equal(existsSync(path.join(path.dirname(installed),'THIRD_PARTY_NOTICES.md')),false)
      const runtime=JSON.parse(await readFile(path.join(data.staging,'runtime.json'),'utf8'))
      assert.deepEqual(runtime.voice,{path:original.path,sha256:createHash('sha256').update(bytes).digest('hex'),platform,arch})
      assert.equal(existsSync(path.join(data.root,'desktop/hex-engine')),false)
      await beforePack({packager:{projectDir:path.join(data.root,'desktop'),appInfo:{version:'fixture'}},electronPlatformName:platform,arch:Arch[arch]})
    })
  }
})

test('release preparation succeeds without a system skills directory',async t=>{
  for(const language of ['en','zh-CN','en']) {
    const data=await fixture(t)
    // 移除系统技能目录，模拟当前无预装技能的检出。
    await rm(path.join(data.root,'.harness','skills'),{recursive:true,force:true})
    const baseExecute=data.options.execute
    data.options.execute=async(command,args,options)=>{
      if(args[1]&&args[1].includes('build_default_registry'))return '[]'
      return baseExecute(command,args,options)
    }
    await prepareRelease({...data.options,language})
    assert.deepEqual(JSON.parse(await readFile(path.join(data.staging,'runtime.json'),'utf8')),
      {version:'fixture',platform:'darwin',arch:'arm64',python:'3.14.10',bundledSkills:[],voice:await expectedVoice(data.root,'darwin','arm64')})
    // 无技能时不写入 site/.harness/skills。
    assert.equal(existsSync(path.join(data.staging,'site/.harness/skills')),false)
    await beforePack({packager:{projectDir:path.join(data.root,'desktop'),appInfo:{version:'fixture'}},electronPlatformName:'darwin',arch:Arch.arm64})
  }
})

test('release preparation preserves external command error identity without retrying',async t=>{
  for(const language of ['en','zh-CN','en']) {
    for(const stage of ['python','electron','dependencies']) {
      const failure=new Error('Original {path} failure 原文',{cause:new Error('Original cause')})
      const failureAt=(command,args)=>stage==='python'?command==='uv'&&args[0]==='python':
        stage==='electron'?args[0].endsWith('install.js'):command==='uv'&&args[0]==='pip'
      const data=await fixture(t,{failure,failureAt})
      await assert.rejects(prepareRelease({...data.options,language}),error=>error===failure)
      assert.equal(data.calls.length,{python:2,electron:4,dependencies:5}[stage])
      assert.deepEqual(data.logs,stage==='python'?[setupCopy(language)('desktop.release.pythonInstallHelp')]:[])
      assert.equal(existsSync(path.join(data.staging,'site')),false)
      assert.equal(existsSync(path.join(data.staging,'runtime.json')),false)
      if(stage==='python')assert.equal(existsSync(path.join(data.root,'desktop/node_modules/electron/dist')),false)
    }
  }
})
