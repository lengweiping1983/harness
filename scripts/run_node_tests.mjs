import {readdirSync} from 'node:fs'
import path from 'node:path'
import {spawnSync} from 'node:child_process'

// 显式收集用例，避免空目录触发 Node 默认发现并运行其他脚本。
function discover(directory) {
  return readdirSync(directory, {withFileTypes: true}).flatMap(entry => {
    const file = path.join(directory, entry.name)
    return entry.isDirectory() ? discover(file) : entry.name.endsWith('.test.mjs') ? [file] : []
  }).sort()
}
const files = discover(path.resolve(process.argv[2] || 'tests'))
if (files.length) {
  const result = spawnSync(process.execPath, ['--test', ...files], {stdio: 'inherit', env: process.env})
  if (result.error) throw result.error
  process.exitCode = result.status ?? 1
} else {
  console.log('Node test framework ready: no tests registered')
}
