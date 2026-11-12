import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {mkdtempSync,readFileSync,readdirSync,rmSync,writeFileSync,existsSync,renameSync} from 'node:fs'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {test} from 'node:test'
import {BrowserDownloads,reserveDownload} from '../downloads.mjs'
import {NativeBrowserError} from '../browser-errors.mjs'

function directory(t) {
  const value=mkdtempSync(path.join(tmpdir(),'harness-download-review-'))
  t.after(()=>rmSync(value,{recursive:true,force:true}))
  return value
}

function item(filename='原文 {name}.bin') {
  const value=new EventEmitter(),calls=[]
  let paused=false,resumable=false
  Object.assign(value,{
    calls,getFilename:()=>filename,getInitiatorOrigin:()=> 'https://fixture.invalid',getTotalBytes:()=>128,getReceivedBytes:()=>0,
    isPaused:()=>paused,canResume:()=>resumable,setSavePath:destination=>{value.destination=destination},
    pause:()=>{calls.push('pause');paused=true;value.emit('updated',{},'progressing')},
    resume:()=>{calls.push('resume');paused=false;resumable=false;value.emit('updated',{},'progressing')},
    cancel:()=>{calls.push('cancel');value.emit('done',{},'cancelled')},
    interrupted:()=>{resumable=true;value.emit('updated',{},'interrupted')},
  })
  return value
}

const code = value => error => error instanceof NativeBrowserError && error.code === 'browser/'+value
// POSIX 把“父路径不是目录”报为 ENOTDIR，Windows 报为 ENOENT；按平台核对底层清理诊断。
const pathErrorPattern = process.platform === 'win32' ? /ENOENT/ : /ENOTDIR/

test('download controls preserve ownership, state and original filename',t=>{
  const downloads=new BrowserDownloads(directory(t)),download=item()
  const id=downloads.add(download,'owner')
  const before=downloads.snapshot('owner')
  assert.equal(before[0].filename,'原文 {name}.bin')
  assert.deepEqual(downloads.snapshot('other'),[])
  for(const action of ['show','pause','resume','cancel'])assert.throws(()=>downloads.action('other',id,action),code('download-missing'))
  assert.throws(()=>downloads.action('owner','stale','show'),code('download-missing'))
  assert.deepEqual(downloads.snapshot('owner'),before)
  assert.deepEqual(download.calls,[])
  assert.throws(()=>downloads.action('owner',id,'show'),code('download-incomplete'))
  assert.throws(()=>downloads.action('owner',id,'resume'),code('download-action'))
  assert.throws(()=>downloads.action('owner',id,'unknown'),code('download-action'))
  downloads.action('owner',id,'pause');downloads.action('owner',id,'resume')
  download.interrupted();downloads.action('owner',id,'resume')
  assert.deepEqual(download.calls,['pause','resume','resume'])
  const bytes=Buffer.from([0,255,10,13,27])
  writeFileSync(download.destination,bytes)
  download.emit('done',{},'completed')
  assert.equal(downloads.action('owner',id,'show'),download.destination)
  assert.throws(()=>downloads.action('owner',id,'cancel'),code('download-stopped'))
  assert.throws(()=>downloads.action('owner',id,'pause'),code('download-stopped'))
  assert.deepEqual(readFileSync(download.destination),bytes)
  assert.deepEqual(download.calls,['pause','resume','resume'])
})

test('cancelling a colliding download preserves the preexisting file and cancels only once',t=>{
  const root=directory(t),original=path.join(root,'原文 {name}.bin'),bytes=Buffer.from('Original bytes 原文')
  writeFileSync(original,bytes)
  const downloads=new BrowserDownloads(root),download=item(),id=downloads.add(download,'owner')
  assert.equal(path.basename(download.destination),'原文 {name} (1).bin')
  downloads.action('owner',id,'cancel')
  assert.equal(existsSync(download.destination),false)
  assert.deepEqual(readFileSync(original),bytes)
  assert.throws(()=>downloads.action('owner',id,'cancel'),code('download-stopped'))
  downloads.close()
  assert.deepEqual(download.calls,['cancel'])
})

test('download reservation failures retain resource identities or external diagnostics',t=>{
  const external=Error('External original {name} 原文')
  for(const error of [new NativeBrowserError('downloadNameLimit'),external]){
    const downloads=new BrowserDownloads(()=>{throw error}),download=item()
    const id=downloads.add(download,'owner'),record=downloads.snapshot('owner')[0]
    assert.equal(record.id,id);assert.equal(record.state,'interrupted');assert.equal(record.done,true)
    assert.deepEqual(record.error,error===external?external.message:error.copy)
    assert.deepEqual(download.calls,['cancel'])
    assert.deepEqual(downloads.snapshot('other'),[])
  }
  const root=directory(t),downloads=new BrowserDownloads(root),download=item()
  download.setSavePath=()=>{throw external}
  downloads.add(download,'owner')
  assert.equal(downloads.snapshot('owner')[0].error,external.message)
  assert.deepEqual(readdirSync(root),[])
})

test('exhausted download names reject without changing any existing files',t=>{
  const root=directory(t),bytes=Buffer.from([0,255,17])
  for(let index=0;index<10000;index++)writeFileSync(path.join(root,index?`Original (${index}).bin`:'Original.bin'),bytes)
  assert.throws(()=>reserveDownload(root,'Original.bin'),code('download-name-limit'))
  assert.equal(readdirSync(root).length,10000)
  assert.deepEqual(readFileSync(path.join(root,'Original.bin')),bytes)
  assert.deepEqual(readFileSync(path.join(root,'Original (9999).bin')),bytes)
})

test('cancel does not delete a replacement file at the reserved download path',t=>{
  const root=directory(t),downloads=new BrowserDownloads(root),download=item(),id=downloads.add(download,'owner')
  const replacement=path.join(root,'replacement'),bytes=Buffer.from('User replacement 原文')
  writeFileSync(replacement,bytes);renameSync(replacement,download.destination)
  downloads.action('owner',id,'cancel')
  assert.equal(existsSync(download.destination),true)
  assert.deepEqual(readFileSync(download.destination),bytes)
})

test('start failure does not delete a replacement file and preserves the original diagnostic',t=>{
  const root=directory(t),downloads=new BrowserDownloads(root),download=item(),bytes=Buffer.from('User replacement 原文')
  const original=Error('Original save path failure')
  download.setSavePath=destination=>{
    download.destination=destination
    const replacement=path.join(root,'replacement')
    writeFileSync(replacement,bytes);renameSync(replacement,destination)
    throw original
  }
  downloads.add(download,'owner')
  assert.deepEqual(readFileSync(download.destination),bytes)
  assert.equal(downloads.snapshot('owner')[0].error,original.message)
})

test('failed reservation cleanup keeps both diagnostics and user replacements',t=>{
  const root=directory(t),parent=path.join(root,'downloads'),moved=path.join(root,'moved')
  const downloads=new BrowserDownloads(parent),download=item(),original=Error('Original save path failure 原文')
  download.setSavePath=()=>{renameSync(parent,moved);writeFileSync(parent,'User parent replacement');throw original}
  downloads.add(download,'owner')
  const error=downloads.snapshot('owner')[0].error
  assert.equal(error.template.key,'browser.desktop.downloadCombinedFailure')
  assert.equal(error.template.params.primary,original.message)
  assert.match(error.template.params.secondary,pathErrorPattern)
  assert.equal(readFileSync(parent,'utf8'),'User parent replacement')
  assert.equal(readdirSync(moved).length,1)
  assert.deepEqual(download.calls,['cancel'])
})

test('cancel cleanup errors are visible without replaying cancellation',t=>{
  const root=directory(t),parent=path.join(root,'downloads'),moved=path.join(root,'moved')
  const downloads=new BrowserDownloads(parent),download=item(),id=downloads.add(download,'owner')
  renameSync(parent,moved);writeFileSync(parent,'User parent replacement')
  downloads.action('owner',id,'cancel')
  const record=downloads.snapshot('owner')[0]
  assert.equal(record.error.template.key,'browser.desktop.downloadCleanupFailure')
  assert.match(record.error.template.params.error,pathErrorPattern)
  assert.equal(record.done,true)
  assert.equal(readFileSync(parent,'utf8'),'User parent replacement')
  assert.deepEqual(download.calls,['cancel'])
})
