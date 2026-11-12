import {open, mkdir, copyFile, writeFile, rm} from 'node:fs/promises'
import {existsSync} from 'node:fs'
import {createHash} from 'node:crypto'
import path from 'node:path'
import {setupCopy} from './setup-copy.mjs'
import {identifyBinary, readEngineManifest, platformTag} from './hex-engine-manifest.mjs'

/** 核对实际可执行文件头，拒绝把其他系统或 CPU 的引擎装入桌面包。
 *  实现收敛到 hex-engine-manifest.mjs，与登记脚本/预检共用同一份 PE/Mach-O 解析。 */
export async function voiceBinaryIdentity(file) {
  return identifyBinary(file)
}

export async function voiceDigest(file) {
  const handle = await open(file, 'r')
  try {
    const hash = createHash('sha256'), buffer = Buffer.alloc(65536)
    let result
    do {result = await handle.read(buffer, 0, buffer.length, null); hash.update(buffer.subarray(0, result.bytesRead))} while (result.bytesRead)
    return hash.digest('hex')
  } finally {await handle.close()}
}

/** 打包必须携带识别引擎与原生粘贴辅助程序，不依赖用户另行安装 Hex。 */
export async function prepareVoice({root, staging, platform, arch, execute, copy, binary = process.env.HARNESS_HEX_BINARY}) {
  const tag = platformTag(platform, arch)
  const filename = platform === 'win32' ? 'hex.exe' : 'hex'
  const relative = path.join('harness/resources/hex', tag, filename)
  // 源码与运行时共用包内资源目录；准备阶段只读取，不再创建第二份源码工作副本。
  const source = binary ? path.resolve(root, binary) : path.join(root, relative)
  if (!existsSync(source)) throw Error(copy('desktop.release.voiceMissing', {path:source}))
  const manifest = !binary ? readEngineManifest(root, tag) : null
  if (!binary && !manifest) throw Error(copy('desktop.release.voiceMismatch', {platform, arch}))
  let identity
  try {identity = await voiceBinaryIdentity(source)} catch (cause) {throw Error(copy('desktop.release.voiceMismatch', {platform, arch}), {cause})}
  if (identity.platform !== platform || identity.arch !== arch) throw Error(copy('desktop.release.voiceMismatch', {platform, arch}))
  const digest = await voiceDigest(source)
  if (manifest && digest !== manifest.sha256) {
    throw Error(copy('desktop.release.voiceMismatch', {platform, arch}),
      {cause: Error(`引擎内容与版本库清单不符: ${tag} 期望 ${manifest.sha256}，实际 ${digest}`)})
  }
  const helpers = path.join(staging, 'voice')
  await mkdir(helpers, {recursive:true})
  if (platform === 'darwin') {
    await execute('/usr/bin/xcrun', ['swiftc', '-O', path.join(root, 'desktop/voice-native.swift'), '-o', path.join(helpers, 'voice-native')], {cwd:root})
    for (const [directory, language] of [['en.lproj','en'],['zh-Hans.lproj','zh-CN']]) {
      await mkdir(path.join(helpers, directory), {recursive:true})
      const localized = setupCopy(language)
      await writeFile(path.join(helpers, directory, 'InfoPlist.strings'),
        ['microphone','camera'].map(type => `"NS${type === 'microphone' ? 'Microphone' : 'Camera'}UsageDescription" = ${JSON.stringify(localized(`desktop.release.${type}Usage`))};\n`).join(''))
    }
  }
  else await copyFile(path.join(root, 'desktop/voice-native.ps1'), path.join(helpers, 'voice-native.ps1'))
  const metadata = {path:relative.replaceAll('\\','/'), sha256:digest, ...identity}
  // 版本与来源随运行时元数据一起写入，verify-runtime 会用它核对打包前的引擎，
  // 让引擎被替换或版本回退在打包阶段就暴露。
  if (manifest) {
    metadata.version = manifest.version
    metadata.source = manifest.source
  }
  await writeFile(path.join(helpers, 'engine.json'), JSON.stringify(metadata) + '\n')
  return metadata
}

/** 显式自备引擎只覆盖安装暂存副本，不修改版本库中的引擎及清单。 */
export async function stageVoiceOverride({root, staging, binary, metadata, copy}) {
  const source = path.resolve(root, binary)
  if (await voiceDigest(source) !== metadata.sha256) {
    throw Error(copy('desktop.release.voiceMismatch', metadata))
  }
  const destination = path.join(staging, 'site', metadata.path)
  await rm(path.dirname(destination), {recursive:true, force:true})
  await mkdir(path.dirname(destination), {recursive:true})
  await copyFile(source, destination)
  // 自备引擎许可证来自同目录或 Hex.app Resources，不沿用默认引擎的声明。
  const directory = path.dirname(source)
  const resources = path.basename(directory) === 'MacOS' && path.basename(path.dirname(directory)) === 'Contents'
    ? path.join(directory, '../Resources') : null
  for (const name of ['LICENSE', 'THIRD_PARTY_NOTICES.md']) {
    const file = [directory, resources].filter(Boolean).map(dir => path.join(dir, name)).find(candidate => existsSync(candidate))
    if (file) await copyFile(file, path.join(path.dirname(destination), name))
  }
}
