#!/usr/bin/env node
/**
 * 把一份合法的 Hex 预编译引擎登记到 harness/resources/hex/<平台>-<架构>/。
 *
 * 用法：
 *   node scripts/vendor-hex-engine.mjs /path/to/hex \
 *     --version 3.3.0 --source 'https://github.com/anomalyco/hex/releases/download/v3.3.0/hex-3.3.0-windows-x64.exe'
 *   node scripts/vendor-hex-engine.mjs /path/to/hex.exe --check
 *
 * 校验 Mach-O / PE 头中的平台与 CPU、计算 sha256、复制许可证、清除 macOS
 * 隔离属性并生成 engine.json。--check 只校验不写入。
 *
 * 版本防漂移：若二进制可在本机执行，会运行 `<引擎> --version` 读取自报版本并与
 * --version 严格比对。历史踩坑是「随版本库分发的预编译引擎 3.3.0」与「早期
 * vendored 源码 3.2.0」并存，手工登记很容易把二进制版本写错；此校验把版本绑定到
 * 二进制本身。仓库已不再保留 Hex 源码，登记是唯一的引擎来源。
 * 跨平台登记（例如 macOS 上登记 Windows PE）无法执行时仅提示，不阻断。
 *
 * 详见 harness/resources/hex/README.md。
 */

import {existsSync, writeFileSync, copyFileSync, mkdirSync, chmodSync} from 'node:fs'
import {spawnSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import path from 'node:path'
import {identifyBinary, sha256File, assertEngineIntegrity, platformTag, engineDir} from '../desktop/hex-engine-manifest.mjs'

const ROOT = fileURLToPath(new URL('../', import.meta.url))
const argv = process.argv.slice(2)
const positional = []
const options = {}
for (let index = 0; index < argv.length; index++) {
  const arg = argv[index]
  if (arg.startsWith('--')) {
    const key = arg.slice(2)
    if (key === 'check') options.check = true
    else options[key] = argv[++index]
  } else positional.push(arg)
}

const source = positional[0] ? path.resolve(positional[0]) : null
if (!source) {
  console.error('用法: node scripts/vendor-hex-engine.mjs <hex 二进制路径> --version <版本> --source <来源> [--check]')
  process.exit(2)
}
if (!existsSync(source)) {
  console.error(`源文件不存在: ${source}`)
  process.exit(2)
}

/** 许可证与第三方声明：优先引擎同目录，其次 Hex.app 的 Resources。 */
function licenseDirs(sourceDir) {
  const dirs = [sourceDir]
  if (path.basename(sourceDir) === 'MacOS' && path.basename(path.dirname(sourceDir)) === 'Contents') {
    dirs.push(path.join(sourceDir, '../Resources'))
  }
  return dirs
}

/** 运行 `<引擎> --version` 读取自报版本；无法执行（跨平台/依赖缺失）时返回 null。 */
function probeVersion(file) {
  const result = spawnSync(file, ['--version'], {encoding: 'utf8', timeout: 15000, windowsHide: true})
  if (result.error || result.status !== 0) return null
  const match = /\bhex\s+(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)\b/i.exec(result.stdout || '')
  return match ? match[1] : null
}

function assertDeclaredVersion(declared, reported) {
  if (!reported) {
    console.log('  · 二进制在本机不可执行（跨平台登记），跳过自报版本比对；请人工核对来源版本。')
    return
  }
  if (reported !== declared) {
    throw Error(`声明版本 ${declared} 与二进制自报版本 ${reported} 不一致；请勿手工登记错误版本。`)
  }
  console.log(`  · 二进制自报版本 ${reported} 与声明一致`)
}

function main() {
  const identity = identifyBinary(source)
  const platform = options.platform || identity.platform
  const arch = options.arch || identity.arch
  if (platform !== identity.platform || arch !== identity.arch) {
    throw Error(`声明的 ${platform}-${arch} 与文件头识别出的 ${identity.platform}-${identity.arch} 不一致`)
  }
  const tag = platformTag(platform, arch)
  const filename = platform === 'win32' ? 'hex.exe' : 'hex'
  const digest = sha256File(source)
  console.log(`识别: ${identity.platform}/${identity.arch} → ${tag}`)
  console.log(`sha256: ${digest}`)

  if (options.check) {
    if (options.version) assertDeclaredVersion(options.version, probeVersion(source))
    // --check 传入已登记目录时可顺带核对现有清单。
    const manifest = path.join(engineDir(ROOT), tag, 'engine.json')
    if (existsSync(manifest)) {
      const checked = assertEngineIntegrity(ROOT, tag)
      console.log(`  · 现有清单 v${checked.version} 完整性校验通过`)
    }
    console.log('\n--check：仅校验，未写入任何文件。')
    return
  }

  if (!options.version) throw Error('缺少 --version（例如 3.3.0）')
  if (!/^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(options.version)) throw Error(`版本格式不合法: ${options.version}`)
  assertDeclaredVersion(options.version, probeVersion(source))

  const dir = path.join(engineDir(ROOT), tag)
  mkdirSync(dir, {recursive: true})
  const destination = path.join(dir, filename)
  if (source !== destination) copyFileSync(source, destination)
  if (platform !== 'win32') chmodSync(destination, 0o755)

  const dirs = licenseDirs(path.dirname(source))
  for (const name of ['LICENSE', 'THIRD_PARTY_NOTICES.md']) {
    const found = dirs.map(candidate => path.join(candidate, name)).find(candidate => existsSync(candidate))
    if (found && path.resolve(found) !== path.resolve(path.join(dir, name))) {
      copyFileSync(found, path.join(dir, name))
      console.log(`复制 ${name}: ${path.relative(ROOT, found)}`)
    }
  }

  if (platform === 'darwin') {
    const result = spawnSync('/usr/bin/xattr', ['-cr', dir], {encoding: 'utf8'})
    if (result.status === 0) console.log('已清除 macOS 隔离属性')
  }

  const metadata = {
    schema: 1,
    version: options.version,
    platform,
    arch,
    sha256: digest,
    source: options.source || 'local',
    vendored_at: new Date().toISOString().slice(0, 10),
    binary: filename,
    notes: '官方预编译 Hex 语音引擎，随源码库分发；由 scripts/vendor-hex-engine.mjs 登记。请勿手工编辑 sha256。',
  }
  writeFileSync(path.join(dir, 'engine.json'), JSON.stringify(metadata, null, 2) + '\n')
  assertEngineIntegrity(ROOT, tag)
  console.log(`\n已登记到 ${path.relative(ROOT, dir)} (v${options.version})，完整性复检通过`)
  console.log('请检查改动后执行: git add harness/resources/hex')
}

try {
  main()
} catch (error) {
  console.error(`\n登记失败: ${error.message}`)
  process.exitCode = 1
}
