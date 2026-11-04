import assert from 'node:assert/strict'
import {test} from 'node:test'
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {sealDevelopmentApp,verifyMacBundle} from '../mac-signing.mjs'
import {setupCopy} from '../setup-copy.mjs'

test('mac development packaging seals the assembled app and validates resources without a certificate',async t=>{
  const root=await mkdtemp(path.join(tmpdir(),'harness-sign-test-'));t.after(()=>rm(root,{recursive:true,force:true}))
  const context={electronPlatformName:'darwin',appOutDir:root,packager:{config:{},appInfo:{id:'test.harness',productFilename:'Harness'}}}
  const seal=path.join(root,'Harness.app/Contents/_CodeSignature/CodeResources'),calls=[]
  const command=async(file,args)=>{
    calls.push([file,args])
    if(args.includes('--sign')){await mkdir(path.dirname(seal),{recursive:true});await writeFile(seal,'seal')}
  }
  await sealDevelopmentApp(context,{command})
  assert.deepEqual(calls.map(([,args])=>args[0]),['--force','--verify'])
  assert.ok(calls[0][1].includes('-'));assert.ok(calls[0][1].includes('test.harness'))
  const count=calls.length
  context.packager.config.forceCodeSigning=true
  await sealDevelopmentApp(context,{command})
  assert.equal(calls.length,count)
  await verifyMacBundle(context,{command})
  assert.equal(calls.length,count+1)
  await sealDevelopmentApp({...context,electronPlatformName:'win32'},{command:assert.fail})
})
test('missing resource seal and native verification failures stop packaging with translated instructions and original cause',async t=>{
  const root=await mkdtemp(path.join(tmpdir(),'harness-seal-error-'));t.after(()=>rm(root,{recursive:true,force:true}))
  const context={electronPlatformName:'darwin',appOutDir:root,packager:{appInfo:{productFilename:'Harness'}}}
  for(const language of ['en','zh-CN','en']) {
    const copy=setupCopy(language)
    await assert.rejects(verifyMacBundle(context,{command:assert.fail,copy}),error=>error.message===copy('desktop.release.signatureInvalid')&&error.cause.code==='ENOENT')
  }
  const seal=path.join(root,'Harness.app/Contents/_CodeSignature/CodeResources')
  await mkdir(path.dirname(seal),{recursive:true});await writeFile(seal,'seal')
  const original=Error('invalid signature')
  await assert.rejects(verifyMacBundle(context,{command:async()=>{throw original}}),error=>error.cause===original)
})

test('packaging configuration registers the full-bundle seal and post-certificate verification hooks',async t=>{
  const {execFile}=await import('node:child_process'),{promisify}=await import('node:util')
  const env={...process.env,HARNESS_LANGUAGE:'en',HARNESS_UPDATE_REPOSITORY:'owner/releases',HARNESS_RELEASE_BUILD:'0'}
  delete env.HARNESS_UPDATE_URL
  await promisify(execFile)(process.execPath,['--input-type=module','-e',`
    import assert from 'node:assert/strict';
    import configure from ${JSON.stringify(new URL('../electron-builder.config.cjs',import.meta.url).href)};
    const config=await configure();
    assert.equal(config.afterPack.name,'sealDevelopmentApp');
    assert.equal(config.afterSign.name,'verifyMacBundle');
    assert.equal(config.files.includes('!mac-signing.mjs'),true);
  `],{env})
})
