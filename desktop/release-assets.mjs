import {createReadStream} from 'node:fs'
import {createHash} from 'node:crypto'
import {mkdir, readdir, readFile, copyFile, writeFile} from 'node:fs/promises'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import yaml from 'js-yaml'

export function mergeManifests(manifests) {
  if (!manifests.length) throw Error('No update manifests')
  const files = new Map()
  for (const manifest of manifests) {
    if (manifest.version !== manifests[0].version || !Array.isArray(manifest.files)) throw Error('Update manifest versions or files do not match')
    for (const file of manifest.files) {
      if (typeof file.url !== 'string' || !/^[\w.-]+$/.test(file.url) || !Number.isSafeInteger(file.size) || file.size <= 0 || typeof file.sha512 !== 'string') throw Error('Invalid update asset')
      const prior = files.get(file.url)
      if (prior && (prior.sha512 !== file.sha512 || prior.size !== file.size)) throw Error('Conflicting update asset: ' + file.url)
      files.set(file.url, file)
    }
  }
  return {...manifests[0], files:[...files.values()].sort((a, b) => a.url.localeCompare(b.url))}
}
async function hashFile(filename) {
  const hash = createHash('sha512')
  let size = 0
  for await (const chunk of createReadStream(filename)) {hash.update(chunk); size += chunk.length}
  return {sha512:hash.digest('base64'), size}
}
export async function assembleAssets(input, output) {
  const directories = (await readdir(input, {withFileTypes:true})).filter(item => item.isDirectory()).map(item => path.join(input, item.name))
  const manifests = new Map()
  const sources = new Map()
  for (const directory of directories) {
    for (const name of await readdir(directory)) {
      if (/^latest.*\.yml$/.test(name)) {
        const manifest = yaml.load(new TextDecoder('utf-8', {fatal:true}).decode(await readFile(path.join(directory, name))))
        const rows = manifests.get(name) || []; rows.push(manifest); manifests.set(name, rows)
      } else if (/\.(exe|zip|dmg|blockmap)$/.test(name)) {
        if (sources.has(name)) throw Error('Duplicate installer asset: ' + name)
        sources.set(name, path.join(directory, name))
      }
    }
  }
  const merged = [...manifests].map(([name, rows]) => [name, mergeManifests(rows)])
  if (!manifests.has('latest.yml') || !manifests.has('latest-mac.yml')) throw Error('Both Windows and macOS update manifests are required')
  const mac = merged.find(([name]) => name === 'latest-mac.yml')[1]
  if (!mac.files.some(file => /-arm64\.zip$/.test(file.url)) || !mac.files.some(file => /-x64\.zip$/.test(file.url))) throw Error('Both macOS architectures are required')
  const versions = new Set(merged.map(([, manifest]) => manifest.version))
  if (versions.size !== 1) throw Error('Installer versions differ across platforms')
  for (const [, manifest] of merged) for (const file of manifest.files) {
    const source = sources.get(file.url)
    if (!source) throw Error('Missing installer: ' + file.url)
    const actual = await hashFile(source)
    if (actual.sha512 !== file.sha512 || actual.size !== file.size) throw Error('Installer checksum mismatch: ' + file.url)
  }
  await mkdir(output, {recursive:true})
  for (const [name, source] of sources) await copyFile(source, path.join(output, name))
  for (const [name, manifest] of merged) await writeFile(path.join(output, name), yaml.dump(manifest))
  console.log(`Validated release ${[...versions][0]}: ${sources.size} assets, ${merged.length} update manifests`)
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (!process.argv[2] || !process.argv[3]) throw Error('Usage: node desktop/release-assets.mjs INPUT_ARTIFACTS OUTPUT_ASSETS')
  await assembleAssets(path.resolve(process.argv[2]), path.resolve(process.argv[3]))
}
