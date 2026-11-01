import assert from 'node:assert/strict'
import {test} from 'node:test'
import {spawnSync} from 'node:child_process'
import {runCommand} from '../setup.mjs'
import {setupCopy} from '../setup-copy.mjs'
import path from 'node:path'

test('failed subprocess retains exit status and raw diagnostic in current resource language',async()=>{
 for(const language of ['en','zh-CN','en']){
  const copy=setupCopy(language)
  const output='Original {output} diagnostic'
  await assert.rejects(runCommand(process.execPath,['-e',`process.stderr.write(${JSON.stringify(output)});process.exitCode=7`],{capture:true,language}),error=>{
   assert.equal(error.message,copy('desktop.setup.commandExitedOutput',{command:path.basename(process.execPath),status:7,output}))
   return true
  })
  assert.equal(await runCommand(process.execPath,['-e','process.stdout.write("Original {literal}")'],{capture:true,language}),'Original {literal}')
  const result=spawnSync(process.execPath,['desktop/launch.mjs','--invalid'],{cwd:new URL('../../',import.meta.url),env:{...process.env,HARNESS_LANGUAGE:language},encoding:'utf8'})
  assert.equal(result.status,1)
  assert.ok(result.stderr.includes(copy('desktop.setup.launchUsage')))
 }
})

test('standalone npm lookup rejects with current resource without launching an installer',()=>{
 for(const language of ['en','zh-CN','en']){
  const module=new URL('../setup.mjs',import.meta.url).href
  const script=`import {npmCLI} from ${JSON.stringify(module)};process.execPath='/missing-harness-fixture/node';try{npmCLI({PATH:'',HARNESS_LANGUAGE:process.argv[1]})}catch(error){console.log(error.message);process.exitCode=1}`
  const result=spawnSync(process.execPath,['--input-type=module','-e',script,language],{encoding:'utf8'})
  assert.equal(result.status,1)
  assert.equal(result.stdout.trim(),setupCopy(language)('desktop.setup.npmMissing'))
 }
})
