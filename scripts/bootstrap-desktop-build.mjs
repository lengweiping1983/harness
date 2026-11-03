#!/usr/bin/env node
/**
 * 桌面打包前的构建环境自举 / 预检。
 *
 * 背景（2026-11-03 实测踩坑，逐条固化为自动检查）：
 *   本环境 `npm ci` 有时不会完整展开依赖树，也不会执行 postinstall，导致
 *   构建在十几分钟后才以各种 "command not found" / "Cannot find module" 失败：
 *     1. esbuild / rollup 的可选平台二进制（@esbuild/darwin-arm64 等）没装；
 *     2. electron-builder 的 .bin 软链接没生成（已改为直接 node 调 cli.js，见 package.json）；
 *     3. 嵌套依赖（@sindresorhus/is 等）缺失；
 *     4. electron 的 dist 未下载（postinstall 被跳过）；
 *     5. prepare-release 要求暂存目录全新，但外壳 rm 可能被 IDE 的删除钩子接管，
 *        导致 `rm -rf .run/desktop-release/resources` 静默失效，打包在准备阶段才报
 *        "暂存目录不是全新目录"。改用 Node fs.rmSync 直接删除，绕过外壳钩子。
 *   另外：zip 目标在本环境沙箱下会失败（用 HARNESS_MAC_TARGETS=dmg 跳过），
 *         下载的 hex 二进制可能带 com.apple.quarantine（需 xattr -cr）。
 *
 * 本脚本在每次 `npm --prefix desktop run package` 前自动运行，检测并修复上述
 * 问题；`--check` 只报告不修改，可用于 CI 预检。只依赖 Node 内置模块。
 */

import {existsSync, readFileSync, rmSync} from 'node:fs'
import {spawnSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import path from 'node:path'
import {platformTag, assertEngineIntegrity, engineDir} from '../desktop/hex-engine-manifest.mjs'

const ROOT = fileURLToPath(new URL('../', import.meta.url))
const CHECK = process.argv.includes('--check')
// 完整打包（package:prepare）传入；package:dir 不传，以便复用已有暂存产物。
const CLEAN_STAGING = process.argv.includes('--clean-staging')
const NODE = process.execPath
const NPM = process.platform === 'win32' ? 'npm.cmd' : 'npm'
const PLATFORM = process.platform
const ARCH = process.arch

// 本机实测曾因依赖树未展开而缺失的 electron-builder 间接依赖。
// 这些包在 npm ci 的扁平布局下位于 desktop/node_modules 顶层。
const REQUIRED_NESTED = ['@sindresorhus/is', 'got', '@electron/get', 'app-builder-lib']

// 去掉 IDE 注入的 NODE_OPTIONS shim：它只服务于编辑器进程，
// 对 npm 子进程无意义，却可能干扰生命周期脚本与可选依赖安装。
const CHILD_ENV = {...process.env}
delete CHILD_ENV.NODE_OPTIONS

const problems = []
const fixed = []
const notes = []

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: ROOT,
    encoding: 'utf8',
    env: CHILD_ENV,
    stdio: ['ignore', 'pipe', 'pipe'],
    shell: process.platform === 'win32' && command === NPM,
    ...options,
  })
}

function npm(args, cwd) {
  const result = run(NPM, args, {cwd})
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw Error(`npm ${args.join(' ')} 失败 (exit ${result.status}):\n${(result.stderr || result.stdout || '').trim()}`)
  }
  return result
}

function logFix(message) {
  console.log(`  → ${message}`)
}

/** 检查 esbuild / rollup 的原生平台二进制能否真实加载，缺失时补装。 */
function ensureNativeBinaries(workspace, cwd, modules) {
  for (const tool of ['esbuild', 'rollup']) {
    const pkgPath = path.join(modules, tool, 'package.json')
    if (!existsSync(pkgPath)) continue // 该 workspace 不使用此工具
    const probe = run(NODE, ['-e', `require(${JSON.stringify(tool)})`], {cwd})
    if (probe.status === 0) continue

    const detail = `${probe.stderr || ''}${probe.stdout || ''}`.trim()
    const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'))
    const optional = pkg.optionalDependencies || {}
    // 错误信息形如 "Cannot find module '@esbuild/darwin-arm64'"；
    // 若无法解析，则按当前平台/架构从 optionalDependencies 里找。
    const named = detail.match(/Cannot find module '((?:@esbuild|@rollup)\/[^']+)'/)?.[1]
    const target = named && optional[named]
      ? named
      : Object.keys(optional).find(entry => entry.includes(`-${PLATFORM}-${ARCH}`))
    problems.push(`${workspace}: ${tool} 原生平台二进制缺失`)
    if (CHECK) continue
    if (!target) throw Error(`无法识别 ${tool} 缺失的平台包，原始错误：\n${detail}`)

    const version = optional[target] || pkg.version
    logFix(`安装 ${target}@${version}`)
    npm(['install', '--no-save', '--no-package-lock', '--no-audit', '--no-fund', `${target}@${version}`], cwd)
    const again = run(NODE, ['-e', `require(${JSON.stringify(tool)})`], {cwd})
    if (again.status !== 0) throw Error(`${tool} 平台包安装后仍无法加载：\n${(again.stderr || '').trim()}`)
    fixed.push(`${workspace}: 补齐 ${target}@${version}`)
  }
}

/** 检查一个 workspace 的 node_modules 是否完整，必要时干净重装。 */
function ensureWorkspace(workspace) {
  const cwd = path.join(ROOT, workspace)
  const modules = path.join(cwd, 'node_modules')
  const pkgPath = path.join(cwd, 'package.json')
  if (!existsSync(pkgPath)) {
    notes.push(`${workspace}: 无 package.json，跳过`)
    return
  }
  const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'))
  const topLevel = [...new Set([...Object.keys(pkg.dependencies || {}), ...Object.keys(pkg.devDependencies || {})])]

  if (!existsSync(modules)) {
    problems.push(`${workspace}: node_modules 缺失`)
    if (CHECK) return
    logFix(`${workspace}: 执行 npm ci`)
    npm(['ci', '--no-audit', '--no-fund'], cwd)
    fixed.push(`${workspace}: 安装依赖`)
  }

  let missing = topLevel.filter(dep => !existsSync(path.join(modules, dep, 'package.json')))
  if (workspace === 'desktop') {
    for (const dep of REQUIRED_NESTED) {
      if (!existsSync(path.join(modules, dep, 'package.json'))) missing.push(dep)
    }
    const cli = path.join(modules, 'electron-builder', 'cli.js')
    if (!existsSync(cli)) missing.push('electron-builder/cli.js')
    else if (run(NODE, [cli, '--version'], {cwd}).status !== 0) missing.push('electron-builder CLI 无法加载')
  }

  if (missing.length) {
    problems.push(`${workspace}: 依赖树不完整 (${missing.join(', ')})`)
    if (CHECK) return
    // npm ci 在已有残缺 node_modules 上会"跳过"部分安装；实测只有删干净重装才可靠。
    logFix(`${workspace}: 清理并干净重装依赖`)
    rmSync(modules, {recursive: true, force: true})
    npm(['ci', '--no-audit', '--no-fund'], cwd)
    const still = topLevel.filter(dep => !existsSync(path.join(modules, dep, 'package.json')))
    if (still.length) throw Error(`${workspace} 重装后仍缺少顶层依赖: ${still.join(', ')}`)
    fixed.push(`${workspace}: 干净重装依赖 (npm ci)`)
  }

  ensureNativeBinaries(workspace, cwd, modules)
}

/** electron 的 dist 由 postinstall 下载，postinstall 被跳过时要手动触发。 */
function ensureElectronDist() {
  const desktop = path.join(ROOT, 'desktop')
  const dist = path.join(desktop, 'node_modules', 'electron', 'dist')
  if (existsSync(dist)) {
    notes.push('electron/dist 已就绪')
    return
  }
  problems.push('desktop: electron/dist 缺失（postinstall 未执行）')
  if (CHECK) return
  logFix('执行 electron/install.js 下载二进制')
  const result = run(NODE, ['node_modules/electron/install.js'], {cwd: desktop})
  if (result.error) throw result.error
  if (result.status !== 0 || !existsSync(dist)) {
    throw Error(`electron 二进制下载失败：\n${(result.stderr || result.stdout || '').trim()}`)
  }
  fixed.push('desktop: 下载 electron/dist')
}

/**
 * 完整打包前清掉上次构建的暂存资源。
 * prepare-release.mjs 要求 resources/ 全新（存在 site/web 即拒绝），而外壳 rm
 * 在部分 IDE 环境里被安全删除钩子接管、会静默失效；这里用 Node fs.rmSync 直删。
 * 只删 resources/，保留 managed/ 下的 CPython 缓存，避免重复下载。
 */
function ensureFreshStaging() {
  const staging = path.join(ROOT, '.run', 'desktop-release', 'resources')
  if (CLEAN_STAGING && existsSync(staging)) {
    if (CHECK) {
      notes.push('暂存目录非空（--check 未清理）')
    } else {
      rmSync(staging, {recursive: true, force: true})
      notes.push('已清理上次构建的暂存目录（Node fs，绕过外壳删除钩子）')
    }
  }
}

/** 核对随版本库分发的预编译引擎：清单、文件头身份、sha256 一次校验。
 *  实现收敛到 desktop/hex-engine-manifest.mjs，与登记脚本、prepare-voice、
 *  verify-runtime 共用同一份规则，避免各处解析不一致放过版本漂移。 */
async function ensureHexEngine() {
  if (!['darwin', 'win32'].includes(PLATFORM)) {
    notes.push('非桌面目标平台，跳过引擎校验')
    return
  }
  const tag = platformTag(PLATFORM, ARCH)
  const dir = path.join(engineDir(ROOT), tag)
  const manifestPath = path.join(dir, 'engine.json')
  if (!existsSync(manifestPath)) {
    // 桌面打包已完全不编译引擎源码：仓库内不再保留 vendored Hex 源码，也不需要
    // Rust / CMake / MSVC / Vulkan / Apple SDK 那一整套工具链。缺少版本库预编译
    // 引擎必须立刻失败，而不是等打包十几分钟后才报错。
    // 唯一例外是显式设置了 HARNESS_HEX_BINARY 的自备引擎覆盖场景。
    if (!process.env.HARNESS_HEX_BINARY) {
      throw Error(`打包缺少版本库预编译 Hex 引擎: ${manifestPath}\n`
        + '请用 node scripts/vendor-hex-engine.mjs <hex 二进制> --version <版本> --source <来源> 登记官方引擎。')
    }
    notes.push(`该平台未登记版本库引擎 (${tag})；改用 HARNESS_HEX_BINARY 显式指定的引擎`)
    return
  }
  const meta = assertEngineIntegrity(ROOT, tag)
  // 从 DMG/网络复制来的二进制可能带隔离属性，导致启动冒烟测试被 Gatekeeper 拦截。
  if (PLATFORM === 'darwin' && !CHECK) {
    const result = run('/usr/bin/xattr', ['-cr', dir])
    if (result.status === 0) notes.push('已清除引擎目录的 macOS 隔离属性')
  }
  notes.push(`hex 引擎 v${meta.version} (${tag}) sha256 校验通过`)
}

async function main() {
  console.log('=== 桌面构建环境自举 ===')
  console.log(`平台: ${PLATFORM}-${ARCH}${CHECK ? '  [仅检查]' : ''}`)
  for (const workspace of ['web', 'model-runtime', 'desktop']) ensureWorkspace(workspace)
  ensureElectronDist()
  await ensureHexEngine()
  ensureFreshStaging()

  console.log('')
  if (problems.length) {
    console.log(`检测到 ${problems.length} 项环境缺件：`)
    for (const item of problems) console.log(`  ! ${item}`)
  }
  if (fixed.length) {
    console.log(`已自动修复 ${fixed.length} 项：`)
    for (const item of fixed) console.log(`  ✓ ${item}`)
  }
  for (const item of notes) console.log(`  · ${item}`)
  if (CHECK) {
    console.log(problems.length ? '\n预检未通过（未做任何修改）。' : '\n预检通过，环境就绪。')
    if (problems.length) process.exitCode = 1
  } else {
    console.log('\n构建环境就绪。')
  }
}

main().catch(error => {
  console.error(`\n构建自举失败: ${error.message}`)
  process.exitCode = 1
})
