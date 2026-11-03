import {createReadStream} from 'node:fs'
import {createHash} from 'node:crypto'
import {mkdir, readdir, readFile, copyFile, writeFile, lstat} from 'node:fs/promises'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import yaml from 'js-yaml'

export function mergeManifests(manifests) {
  if (!manifests.length) throw Error('No update manifests')
  const files = new Map()
  for (const manifest of manifests) {
    if (!manifest || typeof manifest.version !== 'string' || !/^\d+\.\d+\.\d+$/.test(manifest.version) || manifest.version !== manifests[0].version || !Array.isArray(manifest.files) || !manifest.files.length) throw Error('Update manifest versions or files do not match')
    for (const file of manifest.files) {
      if (!file || typeof file.url !== 'string' || !/^[\w.-]+$/.test(file.url) || !Number.isSafeInteger(file.size) || file.size <= 0 || typeof file.sha512 !== 'string' || !/^[A-Za-z0-9+/]{86}==$/.test(file.sha512)) throw Error('Invalid update asset')
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
      if (!/^(latest.*\.yml|.*\.(exe|zip|dmg|blockmap))$/.test(name)) continue
      if (!(await lstat(path.join(directory, name))).isFile()) throw Error('Release assets must be regular files: ' + name)
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
  // 官方 Hex 3.3.0 未提供 macOS x64 资产，随版本库分发的引擎只有 macos-arm64，
  // 发布矩阵因此只要求 macOS arm64；日后登记 macos-x86_64 引擎后可在此恢复双架构。
  if (!mac.files.some(file => /-arm64\.zip$/.test(file.url))) throw Error('macOS arm64 installer is required')
  if (!merged.find(([name]) => name === 'latest.yml')[1].files.some(file => /-win-x64\.exe$/.test(file.url))) throw Error('Windows x64 installer is required')
  const versions = new Set(merged.map(([, manifest]) => manifest.version))
  if (versions.size !== 1) throw Error('Installer versions differ across platforms')
  for (const [, manifest] of merged) for (const file of manifest.files) {
    const source = sources.get(file.url)
    if (!source) throw Error('Missing installer: ' + file.url)
    const actual = await hashFile(source)
    if (actual.sha512 !== file.sha512 || actual.size !== file.size) throw Error('Installer checksum mismatch: ' + file.url)
  }
  const version = [...versions][0]
  const required = [`Harness-${version}-win-x64.exe`, `Harness-${version}-mac-arm64.zip`, `Harness-${version}-mac-arm64.dmg`]
  const referenced = new Set(merged.flatMap(([, manifest]) => manifest.files.map(file => file.url)))
  for (const name of required) {
    if (!sources.has(name) || !referenced.has(name)) throw Error('Missing release asset or manifest entry: ' + name)
    if (!sources.has(name + '.blockmap')) throw Error('Missing blockmap: ' + name)
  }
  for (const name of sources.keys()) {
    const installer = name.replace(/\.blockmap$/, '')
    if (!required.includes(installer)) throw Error('Unexpected release asset: ' + name)
  }
  try {
    if ((await readdir(output)).length) throw Error('Release output must be empty: ' + output)
  } catch (error) {if (error.code !== 'ENOENT') throw error}
  await mkdir(output, {recursive:true})
  const checksums = []
  for (const [name, source] of sources) {
    const before = await hashFile(source)
    await copyFile(source, path.join(output, name))
    const copied = await hashFile(path.join(output, name))
    if (before.sha512 !== copied.sha512 || before.size !== copied.size) throw Error('Asset changed while copying: ' + name)
    for (const [, manifest] of merged) {
      const entry = manifest.files.find(file => file.url === name)
      if (entry && (entry.sha512 !== copied.sha512 || entry.size !== copied.size)) throw Error('Installer changed after validation: ' + name)
    }
    checksums.push(`${Buffer.from(copied.sha512, 'base64').toString('hex')}  ${name}`)
  }
  await writeFile(path.join(output, 'SHA512SUMS'), checksums.sort().join('\n') + '\n')
  for (const [name, manifest] of merged) await writeFile(path.join(output, name), yaml.dump(manifest))
  console.log(`Validated release ${[...versions][0]}: ${sources.size} assets, ${merged.length} update manifests`)
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (!process.argv[2] || !process.argv[3]) throw Error('Usage: node desktop/release-assets.mjs INPUT_ARTIFACTS OUTPUT_ASSETS')
  await assembleAssets(path.resolve(process.argv[2]), path.resolve(process.argv[3]))
}
