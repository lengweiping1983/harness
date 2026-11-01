import {createHash} from 'node:crypto'
import {lstat, readdir, readFile} from 'node:fs/promises'
import path from 'node:path'

/** 内容与清单同时参与身份；无入口或非普通资源不能证明构建完整。 */
export async function buildArtifacts(directory) {
  const hash = createHash('sha256')
  try {
    const root = await lstat(directory)
    if (!root.isDirectory() || root.isSymbolicLink()) return null
    const index = await lstat(path.join(directory, 'index.html'))
    if (!index.isFile() || index.isSymbolicLink() || index.size === 0) return null
    const html = new TextDecoder('utf-8', {fatal:true}).decode(await readFile(path.join(directory, 'index.html')))
    for (const match of html.matchAll(/(?:src|href)=["'](\/assets\/[^"'?#]+)[^"']*["']/g)) {
      const resource = path.resolve(directory, '.' + match[1])
      if (!resource.startsWith(path.resolve(directory) + path.sep)) return null
      const info = await lstat(resource)
      if (!info.isFile() || info.isSymbolicLink()) return null
    }
    async function visit(relative) {
      const filename = path.join(directory, relative)
      const info = await lstat(filename)
      if (info.isSymbolicLink()) throw new Error('invalid artifact')
      if (info.isDirectory()) {
        hash.update(JSON.stringify(['directory', relative]))
        for (const name of (await readdir(filename)).sort()) await visit(path.join(relative, name))
      } else if (info.isFile()) {
        const data = await readFile(filename)
        hash.update(JSON.stringify(['file', relative, data.length]))
        hash.update(data)
      } else throw new Error('invalid artifact')
    }
    await visit('')
    return hash.digest('hex')
  } catch (error) {
    if (error.code === 'ENOENT' || error.message === 'invalid artifact') return null
    throw error
  }
}
