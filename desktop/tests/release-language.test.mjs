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

import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises'
import {existsSync} from 'node:fs'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {prepareRelease} from '../prepare-release.mjs'
import beforePack from '../verify-runtime.mjs'
import {Arch} from 'builder-util'

async function fixture(t,{electron=true,count=1,identity={},failure}={}) {
  const root=await mkdtemp(path.join(tmpdir(),'harness-release-原文-{path}-'))
  t.after(()=>rm(root,{recursive:true,force:true}))
  const staging=path.join(root,'.run/desktop-release/resources')
  await mkdir(path.join(root,'desktop'),{recursive:true})
  await writeFile(path.join(root,'desktop/package.json'),JSON.stringify({version:'fixture'}))
  await mkdir(path.join(root,'web/dist'),{recursive:true})
  await writeFile(path.join(root,'web/dist/index.html'),'Original HTML')
  await mkdir(path.join(root,'.harness/skills'),{recursive:true})
  await writeFile(path.join(root,'.harness/skills/original.md'),'Original skill 原文')
  const calls=[],logs=[]
  const execute=async(command,args,options)=>{
    calls.push({command,args,options})
    if(failure)throw failure
    if(args[0].endsWith('install.js')) {
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
        const expectedCalls={platform:0,staging:0,electron:1,runtimeCount:2}[reason]??3
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
      assert.equal(data.calls.length,7)
      for(const call of data.calls)assert.equal(call.options.language,language)
      assert.ok(data.calls[3].args.includes('--require-hashes'))
      assert.ok(data.calls[4].args.includes('--no-deps'))
      assert.equal(await readFile(path.join(data.staging,'web/index.html'),'utf8'),'Original HTML')
      assert.equal(await readFile(path.join(data.staging,'site/.harness/skills/original.md'),'utf8'),'Original skill 原文')
      assert.deepEqual(JSON.parse(await readFile(path.join(data.staging,'runtime.json'),'utf8')),
        {version:'fixture',platform,arch,python:'3.14.10',bundledSkills:['fixture-skill']})
      await beforePack({packager:{projectDir:path.join(data.root,'desktop'),appInfo:{version:'fixture'}},electronPlatformName:platform,arch:Arch[arch]})
      assert.deepEqual(data.logs,[setupCopy(language)('desktop.release.staged',{path:data.staging})])
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
      {version:'fixture',platform:'darwin',arch:'arm64',python:'3.14.10',bundledSkills:[]})
    // 无技能时不写入 site/.harness/skills。
    assert.equal(existsSync(path.join(data.staging,'site/.harness/skills')),false)
    await beforePack({packager:{projectDir:path.join(data.root,'desktop'),appInfo:{version:'fixture'}},electronPlatformName:'darwin',arch:Arch.arm64})
  }
})

test('release preparation preserves external command error identity without retrying',async t=>{
  for(const language of ['en','zh-CN','en']) {
    const failure=new Error('Original {path} failure 原文',{cause:new Error('Original cause')})
    const data=await fixture(t,{failure})
    await assert.rejects(prepareRelease({...data.options,language}),error=>error===failure)
    assert.equal(data.calls.length,1)
    assert.deepEqual(data.logs,[])
  }
})
