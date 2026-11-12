import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, lstatSync, rmSync } from 'node:fs'
import { dirname, join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'

/** 先发布当前资源、原子替换入口，再移除已不属于当前构建的产物。 */
export function publishWebBuild(source, destination, copy = copyFileSync) {
  source = resolve(source)
  destination = resolve(destination)
  if (source === destination || source.startsWith(destination + sep) || destination.startsWith(source + sep)) throw new Error('构建目录与服务目录不能相同或互相嵌套')
  const current = new Set(['index.html'])
  const index = join(source, 'index.html')
  const html = new TextDecoder('utf-8', {fatal:true}).decode(readFileSync(index))
  for (const match of html.matchAll(/(?:src|href)="(\/assets\/[^"?#]+)[^"]*"/g)) {
    const asset = resolve(source, '.' + match[1])
    if (!asset.startsWith(source + sep) || !existsSync(asset)) throw new Error('构建入口引用的资源不存在：' + match[1])
  }
  const walk = (directory, relative = '') => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const from = join(directory, entry.name)
      if (lstatSync(from).isSymbolicLink()) throw new Error('构建资源不能包含符号链接')
      const name = join(relative, entry.name)
      current.add(name)
      const to = join(destination, name)
      if (entry.isDirectory()) walk(from, name)
      else if (name !== 'index.html') { mkdirSync(dirname(to), { recursive: true }); copy(from, to) }
    }
  }
  walk(source)
  mkdirSync(destination, { recursive: true })
  const temporaryIndex = join(destination, '.index-' + randomUUID() + '.tmp')
  copy(index, temporaryIndex)
  renameSync(temporaryIndex, join(destination, 'index.html'))
  const prune = (directory, relative = '') => {
    for (const entry of readdirSync(directory, {withFileTypes: true})) {
      const name = join(relative, entry.name), filename = join(directory, entry.name)
      if (entry.isDirectory()) {
        prune(filename, name)
        if (!readdirSync(filename).length) rmSync(filename, {recursive: true})
      } else if (!current.has(name)) rmSync(filename)
    }
  }
  prune(destination)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  publishWebBuild(join(root, '.run/web-build'), join(root, 'web/dist'))
  process.stdout.write('当前前端资源已发布；过期构建资源已移除。\n')
}
