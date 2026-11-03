import {open, mkdir, copyFile, writeFile} from 'node:fs/promises'
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
  const destination = path.join(root, relative)
  // 引擎来源优先级：显式环境变量 > 随版本库分发的预编译引擎 > 本机编译产物。
  // desktop/hex-engine/ 是纳入版本库的长期依赖来源，也是唯一带版本清单的来源；
  // harness/resources/hex/ 只是打包期工作副本（被 .gitignore 忽略），不能作为
  // 长期来源，否则清理产物后即无法打包，也无法保证版本。
  const vendored = path.join(root, 'desktop/hex-engine', tag, filename)
  const fromVendored = !binary && existsSync(vendored)
  const source = binary ? path.resolve(root, binary)
    : fromVendored ? vendored : destination
  if (!existsSync(source)) throw Error(copy('desktop.release.voiceMissing', {path:source}))
  // 版本库引擎必须同时带 engine.json 清单；清单里的 sha256 用于证明复制进包的
  // 就是登记过的那份二进制，避免 harness/resources/hex/ 里残留的旧版本被误用。
  const manifest = fromVendored ? readEngineManifest(root, tag) : null
  if (fromVendored && !manifest) throw Error(copy('desktop.release.voiceMismatch', {platform, arch}))
  let identity
  try {identity = await voiceBinaryIdentity(source)} catch (cause) {throw Error(copy('desktop.release.voiceMismatch', {platform, arch}), {cause})}
  if (identity.platform !== platform || identity.arch !== arch) throw Error(copy('desktop.release.voiceMismatch', {platform, arch}))
  await mkdir(path.dirname(destination), {recursive:true})
  if (source !== destination) await copyFile(source, destination)
  // 许可证与第三方声明随识别引擎分发；用户数据和模型不参与构建复制。
  // 依次查引擎同目录（版本库预编译引擎）、Hex.app 的 Contents/Resources。
  const engineDir = path.dirname(source)
  const hexAppResources = path.basename(engineDir) === 'MacOS' && path.basename(path.dirname(engineDir)) === 'Contents'
    ? path.join(engineDir, '../Resources') : null
  const licenseDirs = [engineDir, hexAppResources].filter(Boolean)
  for (const name of ['LICENSE','THIRD_PARTY_NOTICES.md']) {
    const target = path.join(path.dirname(destination), name)
    const file = licenseDirs.map(dir => path.join(dir, name)).find(candidate => existsSync(candidate))
    if (file && path.resolve(file) !== path.resolve(target)) await copyFile(file, target)
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
  const digest = await voiceDigest(destination)
  if (manifest && digest !== manifest.sha256) {
    throw Error(copy('desktop.release.voiceMismatch', {platform, arch}),
      {cause: Error(`引擎内容与版本库清单不符: ${tag} 期望 ${manifest.sha256}，实际 ${digest}`)})
  }
  const metadata = {path:relative.replaceAll('\\','/'), sha256:digest, ...identity}
  // 版本与来源随运行时元数据一起写入，verify-runtime 会用它核对打包前的引擎，
  // 让引擎被替换、版本回退或工作副本残留旧版本在打包阶段就暴露。
  if (manifest) {
    metadata.version = manifest.version
    metadata.source = manifest.source
  }
  await writeFile(path.join(helpers, 'engine.json'), JSON.stringify(metadata) + '\n')
  return metadata
}
