import {open, mkdir, copyFile, writeFile} from 'node:fs/promises'
import {existsSync} from 'node:fs'
import {createHash} from 'node:crypto'
import path from 'node:path'
import {setupCopy} from './setup-copy.mjs'

/** 核对实际可执行文件头，拒绝把其他系统或 CPU 的引擎装入桌面包。 */
export async function voiceBinaryIdentity(file) {
  const handle = await open(file, 'r')
  try {
    const header = Buffer.alloc(64)
    const {bytesRead} = await handle.read(header, 0, header.length, 0)
    if (bytesRead < 64) throw Error('Invalid voice executable')
    if (header.readUInt32LE(0) === 0xfeedfacf) {
      const arch = {0x100000c:'arm64',0x1000007:'x64'}[header.readUInt32LE(4)]
      if (arch) return {platform:'darwin', arch}
    }
    if (header.toString('ascii', 0, 2) === 'MZ') {
      const offset = header.readUInt32LE(60), pe = Buffer.alloc(6)
      if (offset < 64 || offset > 1024 * 1024 || (await handle.read(pe, 0, 6, offset)).bytesRead !== 6 || pe.readUInt32LE(0) !== 0x4550) throw Error('Invalid Windows voice executable')
      const arch = {0x8664:'x64',0xaa64:'arm64'}[pe.readUInt16LE(4)]
      if (arch) return {platform:'win32', arch}
    }
    throw Error('Unsupported voice executable')
  } finally {await handle.close()}
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
  const tag = `${platform === 'darwin' ? 'macos' : 'windows'}-${arch === 'x64' ? 'x86_64' : arch}`
  const filename = platform === 'win32' ? 'hex.exe' : 'hex'
  const relative = path.join('harness/resources/hex', tag, filename)
  const destination = path.join(root, relative)
  // 引擎来源优先级：显式环境变量 > 随版本库分发的预编译引擎 > 本机编译产物。
  // desktop/hex-engine/ 是纳入版本库的长期依赖来源；harness/resources/hex/ 只是
  // 打包期工作副本（被 .gitignore 忽略），不能作为唯一来源，否则清理产物后即无法打包。
  const vendored = path.join(root, 'desktop/hex-engine', tag, filename)
  const source = binary ? path.resolve(root, binary)
    : existsSync(vendored) ? vendored : destination
  if (!existsSync(source)) throw Error(copy('desktop.release.voiceMissing', {path:source}))
  let identity
  try {identity = await voiceBinaryIdentity(source)} catch (cause) {throw Error(copy('desktop.release.voiceMismatch', {platform, arch}), {cause})}
  if (identity.platform !== platform || identity.arch !== arch) throw Error(copy('desktop.release.voiceMismatch', {platform, arch}))
  await mkdir(path.dirname(destination), {recursive:true})
  if (source !== destination) await copyFile(source, destination)
  // 许可证与第三方声明随识别引擎分发；用户数据和模型不参与构建复制。
  // 依次查引擎同目录（版本库预编译引擎）、Hex.app 的 Contents/Resources、
  // 以及 vendor/hex 源码目录，兼容三种来源。
  const engineDir = path.dirname(source)
  const hexAppResources = path.basename(engineDir) === 'MacOS' && path.basename(path.dirname(engineDir)) === 'Contents'
    ? path.join(engineDir, '../Resources') : null
  const licenseDirs = [engineDir, hexAppResources, path.join(root, 'vendor/hex')].filter(Boolean)
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
  const metadata = {path:relative.replaceAll('\\','/'), sha256:await voiceDigest(destination), ...identity}
  await writeFile(path.join(helpers, 'engine.json'), JSON.stringify(metadata) + '\n')
  return metadata
}
