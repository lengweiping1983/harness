import assert from 'node:assert/strict'
import {test} from 'node:test'
import {cp, mkdir, mkdtemp, readlink, realpath, rm, symlink, writeFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {relativizeStagedLinks} from '../prepare-release.mjs'
import {setupCopy} from '../setup-copy.mjs'

// 复制 uv 托管的 CPython 后，绝对链接应改写成包内相对链接，否则 codesign --strict 拒绝封签。
test('staged runtime absolute links are rewritten inside the bundle', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'harness-staged-links-'))
  t.after(() => rm(root, {recursive:true, force:true}))
  const source = path.join(root, 'managed', 'bin')
  await mkdir(source, {recursive:true})
  await writeFile(path.join(source, 'python3.14'), 'fixture runtime')
  await symlink(path.join(source, 'python3.14'), path.join(source, 'python3'))
  const bundle = path.join(root, 'bundle')
  await cp(path.join(root, 'managed'), bundle, {recursive:true, dereference:false})
  await relativizeStagedLinks(bundle, path.join(root, 'managed'))
  const link = path.join(bundle, 'bin', 'python3')
  assert.equal(await readlink(link), 'python3.14')
  assert.equal(await realpath(link), await realpath(path.join(bundle, 'bin', 'python3.14')))
})

// 指向源目录之外的链接无法映射到包内，必须按调用语言给出资源化错误，而不是留下非法链接。
test('links outside the source runtime are rejected in each language', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'harness-staged-links-'))
  t.after(() => rm(root, {recursive:true, force:true}))
  const source = path.join(root, 'managed', 'bin')
  await mkdir(source, {recursive:true})
  await symlink('/usr/bin/true', path.join(source, 'python3'))
  const bundle = path.join(root, 'bundle')
  await cp(path.join(root, 'managed'), bundle, {recursive:true, dereference:false})
  for (const language of ['en', 'zh-CN', 'en']) {
    const copy = setupCopy(language)
    await assert.rejects(() => relativizeStagedLinks(bundle, path.join(root, 'managed'), {copy}), error =>
      error.message === copy('desktop.release.linkOutside', {path:path.join(bundle, 'bin', 'python3')}))
  }
})
