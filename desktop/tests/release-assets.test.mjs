import assert from 'node:assert/strict'
import {test} from 'node:test'
import {createHash} from 'node:crypto'
import {mkdtemp, mkdir, writeFile, readFile, rm, readdir} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import yaml from 'js-yaml'
import {assembleAssets, mergeManifests} from '../release-assets.mjs'

// 官方 Hex 3.3.0 未提供 macOS x64 资产，发布矩阵只剩 Windows x64 与 macOS arm64。
async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'harness-update-assets-'))
  t.after(() => rm(root, {recursive:true, force:true}))
  const input = path.join(root, 'input'), output = path.join(root, 'output')
  for (const platform of ['win-x64','mac-arm64']) {
    const directory = path.join(input, platform)
    await mkdir(directory, {recursive:true})
    const files = []
    for (const extension of platform.startsWith('mac') ? ['zip','dmg'] : ['exe']) {
      const url = `Harness-1.2.3-${platform}.${extension}`, data = Buffer.from(url)
      files.push({url, size:data.length, sha512:createHash('sha512').update(data).digest('base64')})
      await writeFile(path.join(directory, url), data)
      await writeFile(path.join(directory, url + '.blockmap'), 'blockmap fixture')
    }
    await writeFile(path.join(directory, platform.startsWith('mac') ? 'latest-mac.yml' : 'latest.yml'), yaml.dump({version:'1.2.3', files, path:files[0].url, sha512:files[0].sha512}))
  }
  return {input, output}
}

test('assembles macOS arm64 and Windows with hashes, refuses stale output', async t => {
  const {input, output} = await fixture(t)
  await assembleAssets(input, output)
  const mac = yaml.load(await readFile(path.join(output, 'latest-mac.yml'), 'utf8'))
  assert.equal(mac.files.length, 2)
  assert.equal((await readFile(path.join(output, 'SHA512SUMS'), 'utf8')).trim().split('\n').length, 6)
  await assert.rejects(assembleAssets(input, output), /must be empty/)
})

test('missing blockmap, corrupted installer and missing macOS platform fail before publishing manifests', async t => {
  for (const failure of ['blockmap','checksum','architecture']) await t.test(failure, async t => {
    const {input, output} = await fixture(t)
    if (failure === 'blockmap') await rm(path.join(input, 'win-x64/Harness-1.2.3-win-x64.exe.blockmap'))
    if (failure === 'checksum') await writeFile(path.join(input, 'win-x64/Harness-1.2.3-win-x64.exe'), 'corrupt')
    if (failure === 'architecture') await rm(path.join(input, 'mac-arm64'), {recursive:true})
    await assert.rejects(assembleAssets(input, output), /Missing blockmap|checksum mismatch|macOS arm64 installer|Both Windows and macOS/)
    await assert.rejects(readdir(output), {code:'ENOENT'})
  })
})

test('manifest validation rejects unsafe paths, invalid hashes, empty files and version mismatch', () => {
  const entry = {url:'installer.exe', size:1, sha512:createHash('sha512').update('x').digest('base64')}
  const manifest = {version:'1.2.3', files:[entry]}
  for (const value of [null, {version:'1.2.3', files:[]}, {...manifest, files:[{...entry, url:'../installer.exe'}]}, {...manifest, files:[{...entry, sha512:'invalid'}]}, {...manifest, version:'1.2.3-beta.1'}]) assert.throws(() => mergeManifests([value]))
  assert.throws(() => mergeManifests([manifest, {...manifest, version:'1.2.4'}]))
})
