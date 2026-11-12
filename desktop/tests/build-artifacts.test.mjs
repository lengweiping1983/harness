import {test} from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp,mkdir,writeFile,rm,symlink} from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import {buildArtifacts} from '../build-artifacts.mjs'

test('artifact identity includes every file and rejects missing or empty entry',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'harness-artifacts-'));t.after(()=>rm(root,{recursive:true,force:true}))
 assert.equal(await buildArtifacts(root),null)
 await writeFile(path.join(root,'index.html'),'');assert.equal(await buildArtifacts(root),null)
 await writeFile(path.join(root,'index.html'),'<html>original</html>')
 const original=await buildArtifacts(root);assert.match(original,/^[a-f0-9]{64}$/)
 assert.equal(await buildArtifacts(root),original)
 await writeFile(path.join(root,'chunk.js'),'original');const withChunk=await buildArtifacts(root);assert.notEqual(withChunk,original)
 await writeFile(path.join(root,'chunk.js'),'changed');assert.notEqual(await buildArtifacts(root),withChunk)
 await rm(path.join(root,'chunk.js'));assert.equal(await buildArtifacts(root),original)
})
test('directory links cannot certify a build',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'harness-artifact-link-'));t.after(()=>rm(root,{recursive:true,force:true}))
 const target=path.join(root,'target');await mkdir(target)
 await writeFile(path.join(target,'index.html'),'original')
 await symlink(target,path.join(root,'link'),process.platform==='win32'?'junction':'dir')
 assert.equal(await buildArtifacts(path.join(root,'link')),null)
 await writeFile(path.join(root,'index.html'),'original')
 assert.equal(await buildArtifacts(root),null)
})

test('initial entry references must exist before an artifact identity is recorded',async t=>{
 const root=await mkdtemp(path.join(os.tmpdir(),'harness-artifact-entry-'));t.after(()=>rm(root,{recursive:true,force:true}))
 await writeFile(path.join(root,'index.html'),'<script src="/assets/missing.js"></script>')
 assert.equal(await buildArtifacts(root),null)
 await mkdir(path.join(root,'assets'))
 await writeFile(path.join(root,'assets/missing.js'),'original')
 assert.match(await buildArtifacts(root),/^[a-f0-9]{64}$/)
 await writeFile(path.join(root,'index.html'),"<link href='/assets/missing.css?original#fragment'>")
 assert.equal(await buildArtifacts(root),null)
 await writeFile(path.join(root,'assets/missing.css'),'original')
 assert.match(await buildArtifacts(root),/^[a-f0-9]{64}$/)
 await writeFile(path.join(root,'index.html'),'<script src="/assets/../../outside.js"></script>')
 assert.equal(await buildArtifacts(root),null)
})
