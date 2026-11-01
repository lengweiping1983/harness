import assert from 'node:assert/strict'
import {test} from 'node:test'
import {setupLanguage,setupCopy} from '../setup-copy.mjs'

test('setup auto language follows region and manual selection skips native detection',()=>{
 for(const platform of ['darwin','win32','linux']){
  for(const language of ['en','zh-CN'])assert.equal(setupLanguage(language,{platform,execute:()=>{throw Error('must not detect')}}),language)
 }
 for(const [locale,expected] of [['en_CN.UTF-8','zh-CN'],['zh_US.UTF-8','en'],['zh-Hant-TW','zh-CN'],['en_HK','zh-CN'],['pt_MO','zh-CN'],['zh','en'],['C.UTF-8','en']]){
  assert.equal(setupLanguage('auto',{platform:'linux',env:{LANG:locale}}),expected)
  assert.equal(setupLanguage(undefined,{platform:'darwin',execute:(command,args,options)=>{
   assert.equal(command,'/usr/bin/defaults');assert.equal(options.timeout,1000);return locale
  }}),expected)
 }
 assert.equal(setupLanguage('auto',{platform:'linux',env:{LC_ALL:'en_US',LC_TIME:'zh_CN',LANG:'zh_CN'}}),'en')
 for(const [region,expected] of [['CN','zh-CN'],['TW','zh-CN'],['US','en'],['invalid','en']])assert.equal(setupLanguage('auto',{platform:'win32',execute:(command,args,options)=>{
  assert.equal(command,'powershell.exe');assert.ok(args.at(-1).includes('GetUserDefaultGeoName'));assert.equal(options.timeout,1000);return region
 }}),expected)
 for(const platform of ['darwin','win32'])assert.equal(setupLanguage('auto',{platform,execute:()=>{throw Error('native timeout')}}),'en')
 assert.equal(setupCopy('auto',{platform:'linux',env:{LANG:'en_CN'}})('desktop.setup.ready'),'Harness Desktop 已就绪。')
})
