#!/usr/bin/env node
/**
 * 把一份合法的 Hex 预编译引擎登记到 desktop/hex-engine/<平台>-<架构>/。
 *
 * 用法：
 *   node scripts/vendor-hex-engine.mjs /path/to/hex \
 *     --version 3.3.0 --source 'https://anoma.ly/hex/download/macos-arm64-dmg'
 *   node scripts/vendor-hex-engine.mjs /path/to/Hex.app/Contents/MacOS/hex --check
 *
 * 会校验 Mach-O / PE 头中的平台与 CPU、计算 sha256、复制许可证、
 * 清除 macOS 隔离属性并生成 engine.json。--check 只校验不写入。
 * 详见 desktop/hex-engine/README.md。
 */

import {existsSync, readFileSync, writeFileSync, copyFileSync, mkdirSync, chmodSync, openSync, readSync, closeSync} from 'node:fs'
import {createHash} from 'node:crypto'
import {createReadStream} from 'node:fs'
import {spawnSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import path from 'node:path'

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

/** 读取文件头，识别 macOS Mach-O / Windows PE 的平台与 CPU 架构。 */
function identify(file) {
  const fd = openSync(file, 'r')
  try {
    const header = Buffer.alloc(128)
    readSync(fd, header, 0, header.length, 0)
    if (header.readUInt32LE(0) === 0xfeedfacf || header.readUInt32BE(0) === 0xfeedfacf) {
      const code = header.readUInt32LE(0) === 0xfeedfacf
        ? header.readUInt32LE(4) : header.readUInt32BE(4)
      const arch = {0x100000c: 'arm64', 0x1000007: 'x64'}[code]
      if (arch) return {platform: 'darwin', arch}
    }
    if (header.toString('ascii', 0, 2) === 'MZ') {
      const offset = header.readUInt32LE(60)
      if (offset < 64 || offset > 1024 * 1024 || header.readUInt32LE(offset) !== 0x4550) {
        throw Error('无效的 Windows 可执行文件')
      }
      const arch = {0x8664: 'x64', 0xaa64: 'arm64'}[header.readUInt16LE(offset + 4)]
      if (arch) return {platform: 'win32', arch}
    }
    throw Error('不是可识别的 macOS / Windows 可执行文件')
  } finally {closeSync(fd)}
}

function sha256(file) {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha256')
    const stream = createReadStream(file)
    stream.on('data', chunk => hash.update(chunk))
    stream.on('error', reject)
    stream.on('end', () => resolve(hash.digest('hex')))
  })
}

/** 许可证与第三方声明：优先引擎同目录，其次 Hex.app 的 Resources，最后源码目录。 */
function licenseDirs(sourceDir) {
  const dirs = [sourceDir]
  if (path.basename(sourceDir) === 'MacOS' && path.basename(path.dirname(sourceDir)) === 'Contents') {
    dirs.push(path.join(sourceDir, '../Resources'))
  }
  dirs.push(path.join(ROOT, 'vendor', 'hex'))
  return dirs
}

async function main() {
  const identity = identify(source)
  const platform = options.platform || identity.platform
  const arch = options.arch || identity.arch
  if (platform !== identity.platform || arch !== identity.arch) {
    throw Error(`声明的 ${platform}-${arch} 与文件头识别出的 ${identity.platform}-${identity.arch} 不一致`)
  }
  const tag = `${platform === 'darwin' ? 'macos' : 'windows'}-${arch === 'x64' ? 'x86_64' : arch}`
  const filename = platform === 'win32' ? 'hex.exe' : 'hex'
  const digest = await sha256(source)
  console.log(`识别: ${identity.platform}/${identity.arch} → ${tag}`)
  console.log(`sha256: ${digest}`)
  if (options.check) {
    console.log('\n--check：仅校验，未写入任何文件。')
    return
  }

  if (!options.version) throw Error('缺少 --version（例如 3.3.0）')
  const dir = path.join(ROOT, 'desktop', 'hex-engine', tag)
  mkdirSync(dir, {recursive: true})
  const destination = path.join(dir, filename)
  copyFileSync(source, destination)
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
  console.log(`\n已登记到 ${path.relative(ROOT, dir)}`)
  console.log('请检查改动后执行: git add desktop/hex-engine')
}

main().catch(error => {
  console.error(`\n登记失败: ${error.message}`)
  process.exitCode = 1
})
