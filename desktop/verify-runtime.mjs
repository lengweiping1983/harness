import {readFile, access} from 'node:fs/promises'
import path from 'node:path'
import {Arch} from 'builder-util'
import {setupCopy} from './setup-copy.mjs'
import {voiceBinaryIdentity, voiceDigest} from './prepare-voice.mjs'
import {platformTag, readEngineManifest} from './hex-engine-manifest.mjs'

export function verifyRuntime(metadata, {platform, arch, version, language = process.env.HARNESS_LANGUAGE}) {
  const copy = setupCopy(language)
  // 允许空技能清单：无预装技能时 bundledSkills 为 []；仍拒绝非数组或含空白名称的无效清单。
  if (!Array.isArray(metadata.bundledSkills) || metadata.bundledSkills.some(name => typeof name !== 'string' || !name.trim())) throw Error(copy('desktop.release.skillsMissing'))
  if (metadata.platform !== platform || metadata.arch !== arch || metadata.version !== version) throw Error(copy('desktop.release.bundleMismatch', {platform, arch, version}))
}
export default async function beforePack(context) {
  const root = path.resolve(context.packager.projectDir, '..')
  const resources = path.resolve(root, '.run/desktop-release/resources')
  const metadata = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(await readFile(path.join(resources, 'runtime.json'))))
  verifyRuntime(metadata, {platform:context.electronPlatformName, arch:Arch[context.arch], version:context.packager.appInfo.version})
  const tag = platformTag(metadata.platform, metadata.arch)
  const relative = `harness/resources/hex/${tag}/${metadata.platform === 'win32' ? 'hex.exe' : 'hex'}`
  const binary = path.join(resources, 'site', relative)
  const identity = await voiceBinaryIdentity(binary)
  const mismatch = () => Error(setupCopy()('desktop.release.voiceMismatch', {platform:context.electronPlatformName, arch:Arch[context.arch]}))
  if (metadata.voice?.path !== relative || metadata.voice.platform !== identity.platform || metadata.voice.arch !== identity.arch || metadata.voice.sha256 !== await voiceDigest(binary)) throw mismatch()
  // 若准备阶段是从版本库预编译引擎取件（运行时元数据带 version），打包前必须确认
  // 该平台的 engine.json 仍声明同一版本与同一 sha256，防止引擎被替换或版本回退。
  if (metadata.voice.version) {
    const manifest = readEngineManifest(root, tag)
    if (!manifest || manifest.version !== metadata.voice.version || manifest.sha256 !== metadata.voice.sha256) {
      throw Error(mismatch().message, {cause: Error(
        `引擎版本/内容与版本库清单不符 (${tag}): 运行时 v${metadata.voice.version} (${metadata.voice.sha256})，` +
        `清单 ${manifest ? `v${manifest.version} (${manifest.sha256})` : '缺失'}`)})
    }
  }
  await access(path.join(resources, 'voice', metadata.platform === 'win32' ? 'voice-native.ps1' : 'voice-native'))
  for (const file of ['web/index.html', 'web/voice.html', 'site/harness/cli.py', metadata.platform === 'win32' ? 'python/python.exe' : 'python/bin/python3']) await access(path.join(resources, file))
  // 仅在实际打包了技能时核对技能目录；无技能安装包不包含该路径。
  if (metadata.bundledSkills.length) await access(path.join(resources, 'site/.harness/skills'))
}
