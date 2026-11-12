import assert from 'node:assert/strict'
import {test} from 'node:test'
import {mkdtemp, mkdir, writeFile, readFile, rm, stat, copyFile} from 'node:fs/promises'
import {existsSync} from 'node:fs'
import {spawnSync} from 'node:child_process'
import {createHash} from 'node:crypto'
import path from 'node:path'
import {tmpdir} from 'node:os'
import {Arch} from 'builder-util'

import {
  platformTag, tagIdentity, engineFilename, identifyBinary, sha256File,
  readEngineManifest, assertEngineIntegrity,
} from '../hex-engine-manifest.mjs'
import {prepareVoice, voiceDigest} from '../prepare-voice.mjs'
import beforePack from '../verify-runtime.mjs'

/** 构造最小 PE，e_lfanew 落在 280（官方 3.3.0 Windows 引擎的真实值）。
 *  旧实现在 128 字节缓冲上读 readUInt32LE(280) 会越界，此 fixture 用作回归。 */
function fakePE(machine = 0x8664, lfanew = 280) {
  const bytes = Buffer.alloc(1024)
  bytes.write('MZ', 0)
  bytes.writeUInt32LE(lfanew, 60)
  bytes.writeUInt32LE(0x4550, lfanew)
  bytes.writeUInt16LE(machine, lfanew + 4)
  return bytes
}

function digest(buffer) {
  return createHash('sha256').update(buffer).digest('hex')
}

async function miniRoot(t, {version = '3.3.0', source = 'https://example.invalid/hex-3.3.0-windows-x64.exe', pe = fakePE(), declaredSha256} = {}) {
  const root = await mkdtemp(path.join(tmpdir(), 'harness-hex-manifest-'))
  t.after(() => rm(root, {recursive: true, force: true}))
  const tag = 'windows-x86_64'
  const engine = path.join(root, 'harness', 'resources', 'hex', tag)
  await mkdir(engine, {recursive: true})
  const binary = path.join(engine, 'hex.exe')
  await writeFile(binary, pe)
  await writeFile(path.join(engine, 'engine.json'), JSON.stringify({
    schema: 1, version, platform: 'win32', arch: 'x64',
    sha256: declaredSha256 ?? digest(pe), source, binary: 'hex.exe',
  }, null, 2) + '\n')
  await mkdir(path.join(root, 'desktop'), {recursive: true})
  await writeFile(path.join(root, 'desktop', 'voice-native.ps1'), '# fixture helper')
  const staging = path.join(root, '.run', 'desktop-release', 'resources')
  await mkdir(staging, {recursive: true})
  return {root, tag, engine, binary, pe, staging}
}

test('platformTag / tagIdentity / engineFilename 与运行时查找路径一致', () => {
  assert.equal(platformTag('win32', 'x64'), 'windows-x86_64')
  assert.equal(platformTag('darwin', 'arm64'), 'macos-arm64')
  assert.deepEqual(tagIdentity('windows-x86_64'), {platform: 'win32', arch: 'x64'})
  assert.equal(tagIdentity('nonsense'), null)
  assert.equal(engineFilename('win32'), 'hex.exe')
  assert.equal(engineFilename('darwin'), 'hex')
})

test('identifyBinary 正确解析 e_lfanew > 128 的 PE（旧 128 字节缓冲越界回归）', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'harness-hex-ident-'))
  t.after(() => rm(root, {recursive: true, force: true}))
  const file = path.join(root, 'hex.exe')
  await writeFile(file, fakePE())
  assert.deepEqual(identifyBinary(file), {platform: 'win32', arch: 'x64'})
  await writeFile(file, fakePE(0xaa64))
  assert.deepEqual(identifyBinary(file), {platform: 'win32', arch: 'arm64'})
  const macho = Buffer.alloc(128)
  macho.writeUInt32LE(0xfeedfacf, 0)
  macho.writeUInt32LE(0x100000c, 4)
  await writeFile(file, macho)
  assert.deepEqual(identifyBinary(file), {platform: 'darwin', arch: 'arm64'})
})

test('assertEngineIntegrity 只校验当前平台，缺清单/内容不符/身份不符/版本非法时拒绝', async t => {
  const data = await miniRoot(t)
  assert.equal(assertEngineIntegrity(data.root, 'windows-x86_64').version, '3.3.0')
  // 其他平台没有清单也不影响 Windows 的校验：不做跨平台版本比较。
  assert.equal(readEngineManifest(data.root, 'macos-arm64'), null)

  await writeFile(data.binary, fakePE(0xaa64))
  assert.throws(() => assertEngineIntegrity(data.root, 'windows-x86_64'), /身份不匹配/)
  await writeFile(data.binary, data.pe)
  await writeFile(path.join(data.engine, 'engine.json'), JSON.stringify({version: 'not-a-version', sha256: digest(data.pe)}))
  assert.throws(() => assertEngineIntegrity(data.root, 'windows-x86_64'), /版本格式不合法/)
  await writeFile(path.join(data.engine, 'engine.json'), JSON.stringify({version: '3.3.0', sha256: '0'.repeat(64)}))
  assert.throws(() => assertEngineIntegrity(data.root, 'windows-x86_64'), /sha256 不匹配/)
  assert.throws(() => assertEngineIntegrity(data.root, 'windows-arm64'), /缺少该平台的引擎清单/)
})

test('prepareVoice 直接读取唯一资源目录，保留源文件身份并写入 version/source', async t => {
  const data = await miniRoot(t)
  const original = await stat(data.binary)
  const metadata = await prepareVoice({
    root: data.root, staging: data.staging, platform: 'win32', arch: 'x64',
    execute: async () => {}, copy: key => key, binary: null,
  })
  assert.equal(metadata.version, '3.3.0')
  assert.equal(metadata.source, 'https://example.invalid/hex-3.3.0-windows-x64.exe')
  assert.equal(metadata.sha256, digest(data.pe))
  assert.deepEqual({platform: metadata.platform, arch: metadata.arch}, {platform: 'win32', arch: 'x64'})
  assert.equal(metadata.path, 'harness/resources/hex/windows-x86_64/hex.exe')
  const current = await stat(data.binary)
  assert.deepEqual([current.ino, current.mtimeMs], [original.ino, original.mtimeMs])
  assert.equal(await voiceDigest(data.binary), digest(data.pe))
  assert.equal(existsSync(path.join(data.root, 'desktop', 'hex-engine')), false)
  assert.deepEqual(JSON.parse(await readFile(path.join(data.staging, 'voice', 'engine.json'), 'utf8')), metadata)
  assert.equal(await readFile(path.join(data.staging, 'voice', 'voice-native.ps1'), 'utf8'), '# fixture helper')
})

test('prepareVoice 拒绝唯一资源目录缺失清单，不将裸引擎作为回退来源', async t => {
  const data = await miniRoot(t)
  await rm(path.join(data.engine, 'engine.json'))
  await assert.rejects(prepareVoice({
    root: data.root, staging: data.staging, platform: 'win32', arch: 'x64',
    execute: async () => {}, copy: key => key, binary: null,
  }), /voiceMismatch/)
  assert.equal(await voiceDigest(data.binary), digest(data.pe))
})

test('prepareVoice 拒绝清单 sha256 与实际引擎不符的版本漂移', async t => {
  const data = await miniRoot(t, {declaredSha256: 'a'.repeat(64)})
  await assert.rejects(prepareVoice({
    root: data.root, staging: data.staging, platform: 'win32', arch: 'x64',
    execute: async () => {}, copy: key => key, binary: null,
  }), error => /voiceMismatch/.test(error.message) && /引擎内容/.test(error.cause?.message))
})

test('登记脚本在唯一资源目录内重新登记时保留二进制并更新清单', async t => {
  const data = await miniRoot(t)
  await mkdir(path.join(data.root, 'scripts'))
  const script = path.join(data.root, 'scripts', 'vendor-hex-engine.mjs')
  await copyFile(new URL('../../scripts/vendor-hex-engine.mjs', import.meta.url), script)
  await copyFile(new URL('../hex-engine-manifest.mjs', import.meta.url), path.join(data.root, 'desktop', 'hex-engine-manifest.mjs'))
  const original = await stat(data.binary)
  const result = spawnSync(process.execPath, [script, data.binary, '--version', '3.3.0', '--source', 'fixture-source'], {encoding:'utf8'})
  assert.equal(result.status, 0, result.stdout + result.stderr)
  assert.equal((await stat(data.binary)).ino, original.ino)
  const manifest = assertEngineIntegrity(data.root, data.tag)
  assert.equal(manifest.source, 'fixture-source')
  assert.equal(manifest.sha256, digest(data.pe))
  assert.equal(existsSync(path.join(data.root, 'desktop', 'hex-engine')), false)
})

test('verify-runtime 打包前核对引擎版本与版本库清单，不匹配时拒绝', async t => {
  const data = await miniRoot(t)
  const metadata = await prepareVoice({
    root: data.root, staging: data.staging, platform: 'win32', arch: 'x64',
    execute: async () => {}, copy: key => key, binary: null,
  })
  // 构造 beforePack 需要的其余最小资源。
  const resources = data.staging
  const siteHex = path.join(resources, 'site', metadata.path)
  await mkdir(path.dirname(siteHex), {recursive: true})
  await writeFile(siteHex, data.pe)
  await writeFile(path.join(resources, 'site', 'harness', 'cli.py'), '# fixture')
  await mkdir(path.join(resources, 'web'), {recursive: true})
  await writeFile(path.join(resources, 'web', 'index.html'), 'index')
  await writeFile(path.join(resources, 'web', 'voice.html'), 'voice')
  await mkdir(path.join(resources, 'python'), {recursive: true})
  await writeFile(path.join(resources, 'python', 'python.exe'), 'fixture')
  const runtimePath = path.join(resources, 'runtime.json')
  const context = {
    packager: {projectDir: path.join(data.root, 'desktop'), appInfo: {version: '0.1.2'}},
    electronPlatformName: 'win32', arch: Arch.x64,
  }
  const writeRuntime = voice => writeFile(runtimePath, JSON.stringify({
    version: '0.1.2', platform: 'win32', arch: 'x64', python: '3.14.10', bundledSkills: [], voice,
  }))
  await writeRuntime(metadata)
  await assert.doesNotReject(beforePack(context))

  // 运行时元数据被改成旧版本 → 与版本库清单不一致，拒绝打包。
  await writeRuntime({...metadata, version: '3.2.0'})
  await assert.rejects(beforePack(context), /语音引擎|voice engine|voiceMismatch|不一致/)
  // 版本库清单被升级但运行时元数据仍是旧版 → 同样拒绝。
  await writeRuntime(metadata)
  await writeFile(path.join(data.engine, 'engine.json'), JSON.stringify({
    version: '3.4.0', platform: 'win32', arch: 'x64', sha256: digest(data.pe), binary: 'hex.exe',
  }))
  await assert.rejects(beforePack(context), /语音引擎|voice engine|voiceMismatch|不一致/)
})
