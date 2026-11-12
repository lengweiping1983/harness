/**
 * 随版本库分发的 Hex 语音引擎清单读取与完整性校验（构建期使用）。
 *
 * 单一事实来源是 harness/resources/hex/<tag>/engine.json，由 scripts/vendor-hex-engine.mjs
 * 登记：version / source / sha256 / binary / platform / arch。本模块只负责
 * 「当前打包平台」的清单读取、文件头身份识别与内容核对，不跨平台比较版本。
 *
 * 打包链路（bootstrap → prepare-voice → verify-runtime）共享这里的实现，
 * 避免每个脚本各写一份 PE 解析（历史上 128 字节缓冲读 e_lfanew=280 会越界），
 * 并让版本号真正进入校验链。校验只针对当前打包平台，不跨平台比较版本。
 * 不依赖三方模块，可在 beforePack 阶段加载。
 */

import {existsSync, readFileSync, openSync, readSync, closeSync} from 'node:fs'
import {createHash} from 'node:crypto'
import path from 'node:path'
import {fileURLToPath} from 'node:url'

export const REPO_ROOT = fileURLToPath(new URL('../', import.meta.url))

/** 平台标签与 harness/tools/builtin/hex_tools.py 的 _platform_tag() 保持一致。 */
export function platformTag(platform, arch) {
  const system = {darwin: 'macos', win32: 'windows', linux: 'linux'}[platform] || platform
  return `${system}-${arch === 'x64' ? 'x86_64' : arch}`
}

export function engineFilename(platform) {
  return platform === 'win32' ? 'hex.exe' : 'hex'
}

export function engineDir(root = REPO_ROOT) {
  return path.join(root, 'harness', 'resources', 'hex')
}

/** 从标签反推平台/架构，用于核对二进制文件头：windows-x86_64 → win32/x64。 */
export function tagIdentity(tag) {
  const match = /^(macos|windows|linux)-(x86_64|arm64)$/.exec(tag)
  if (!match) return null
  return {
    platform: match[1] === 'macos' ? 'darwin' : match[1] === 'windows' ? 'win32' : match[1],
    arch: match[2] === 'x86_64' ? 'x64' : 'arm64',
  }
}

/**
 * 读取文件头识别 Mach-O / PE 的平台与 CPU。
 * PE 的 e_lfanew 常大于 128（官方 3.3.0 Windows 引擎为 280），必须在偏移处二次读取，
 * 不能在固定 128 字节缓冲上索引，否则 RangeError 导致登记/打包失败。
 */
export function identifyBinary(file) {
  const fd = openSync(file, 'r')
  try {
    const header = Buffer.alloc(64)
    if (readSync(fd, header, 0, header.length, 0) < header.length) throw Error('Invalid voice executable')
    for (const readUInt32 of ['readUInt32LE', 'readUInt32BE']) {
      if (header[readUInt32](0) === 0xfeedfacf) {
        const arch = {0x100000c: 'arm64', 0x1000007: 'x64'}[header[readUInt32](4)]
        if (arch) return {platform: 'darwin', arch}
      }
    }
    if (header.toString('ascii', 0, 2) === 'MZ') {
      const offset = header.readUInt32LE(60)
      if (offset < 64 || offset > 1024 * 1024) throw Error('Invalid Windows voice executable')
      const pe = Buffer.alloc(6)
      if (readSync(fd, pe, 0, pe.length, offset) !== pe.length || pe.readUInt32LE(0) !== 0x4550) {
        throw Error('Invalid Windows voice executable')
      }
      const arch = {0x8664: 'x64', 0xaa64: 'arm64'}[pe.readUInt16LE(4)]
      if (arch) return {platform: 'win32', arch}
    }
    throw Error('Unsupported voice executable')
  } finally {
    closeSync(fd)
  }
}

export function sha256File(file) {
  return createHash('sha256').update(readFileSync(file)).digest('hex')
}

/** 读取某平台的 engine.json；不存在返回 null，字段不完整直接抛错。 */
export function readEngineManifest(root, tag) {
  const file = path.join(engineDir(root), tag, 'engine.json')
  if (!existsSync(file)) return null
  const meta = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(readFileSync(file)))
  if (typeof meta?.version !== 'string' || typeof meta?.sha256 !== 'string') {
    throw Error(`引擎清单字段不完整: ${file}`)
  }
  return {...meta, file, tag, binary: meta.binary || engineFilename(tagIdentity(tag)?.platform)}
}

/**
 * 单平台完整性校验：清单存在 → 二进制存在 → 文件头身份与标签一致 → sha256 匹配。
 * 返回清单对象（含 version/source），供 prepare-voice 写入运行时元数据。
 * 只校验传入的 tag，不枚举/比较其他平台。
 */
export function assertEngineIntegrity(root, tag, {verifySha256 = true} = {}) {
  const manifest = readEngineManifest(root, tag)
  if (!manifest) throw Error(`缺少该平台的引擎清单: ${path.join(engineDir(root), tag, 'engine.json')}；请用 scripts/vendor-hex-engine.mjs 登记预编译引擎。`)
  if (!/^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(manifest.version)) {
    throw Error(`引擎版本格式不合法: ${manifest.version}`)
  }
  const expected = tagIdentity(tag)
  if (!expected) throw Error(`无法识别的引擎平台标签: ${tag}`)
  const binary = path.join(path.dirname(manifest.file), manifest.binary)
  if (!existsSync(binary)) throw Error(`引擎清单声明的二进制不存在: ${binary}`)
  const identity = identifyBinary(binary)
  if (identity.platform !== expected.platform || identity.arch !== expected.arch) {
    throw Error(`引擎二进制身份不匹配: ${tag} 需要 ${expected.platform}/${expected.arch}，实际 ${identity.platform}/${identity.arch}`)
  }
  if (manifest.platform && (manifest.platform !== identity.platform || manifest.arch !== expected.arch)) {
    throw Error(`引擎清单声明的平台/架构与文件不符: ${tag}`)
  }
  if (verifySha256) {
    const actual = sha256File(binary)
    if (actual !== manifest.sha256) {
      throw Error(`引擎 sha256 不匹配 (${tag})：期望 ${manifest.sha256}，实际 ${actual}。常见原因是 git 把二进制当文本做了换行转换，请确认 .gitattributes 的 binary 标记并重新登记。`)
    }
  }
  return manifest
}

/** CLI：node desktop/hex-engine-manifest.mjs --check [root] [tag] 供 CI / 本地预检。 */
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2).filter(value => !value.startsWith('--'))
  const root = args[0] ? path.resolve(args[0]) : REPO_ROOT
  const tag = args[1] || platformTag(process.platform, process.arch)
  try {
    const manifest = assertEngineIntegrity(root, tag)
    console.log(`Hex 引擎校验通过: ${tag} v${manifest.version} (${manifest.binary})`)
  } catch (error) {
    console.error(`Hex 引擎校验失败: ${error.message}`)
    process.exitCode = 1
  }
}
