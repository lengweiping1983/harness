import {readFile, access} from 'node:fs/promises'
import path from 'node:path'
import {Arch} from 'builder-util'
import {setupCopy} from './setup-copy.mjs'
import {voiceBinaryIdentity, voiceDigest} from './prepare-voice.mjs'

export function verifyRuntime(metadata, {platform, arch, version, language = process.env.HARNESS_LANGUAGE}) {
  const copy = setupCopy(language)
  // 允许空技能清单：无预装技能时 bundledSkills 为 []；仍拒绝非数组或含空白名称的无效清单。
  if (!Array.isArray(metadata.bundledSkills) || metadata.bundledSkills.some(name => typeof name !== 'string' || !name.trim())) throw Error(copy('desktop.release.skillsMissing'))
  if (metadata.platform !== platform || metadata.arch !== arch || metadata.version !== version) throw Error(copy('desktop.release.bundleMismatch', {platform, arch, version}))
}
export default async function beforePack(context) {
  const resources = path.resolve(context.packager.projectDir, '../.run/desktop-release/resources')
  const metadata = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(await readFile(path.join(resources, 'runtime.json'))))
  verifyRuntime(metadata, {platform:context.electronPlatformName, arch:Arch[context.arch], version:context.packager.appInfo.version})
  const tag = `${metadata.platform === 'darwin' ? 'macos' : 'windows'}-${metadata.arch === 'x64' ? 'x86_64' : metadata.arch}`
  const relative = `harness/resources/hex/${tag}/${metadata.platform === 'win32' ? 'hex.exe' : 'hex'}`
  const binary = path.join(resources, 'site', relative)
  const identity = await voiceBinaryIdentity(binary)
  if (metadata.voice?.path !== relative || metadata.voice.platform !== identity.platform || metadata.voice.arch !== identity.arch || metadata.voice.sha256 !== await voiceDigest(binary)) throw Error(setupCopy()('desktop.release.voiceMismatch', {platform:context.electronPlatformName, arch:Arch[context.arch]}))
  await access(path.join(resources, 'voice', metadata.platform === 'win32' ? 'voice-native.ps1' : 'voice-native'))
  for (const file of ['web/index.html', 'web/voice.html', 'site/harness/cli.py', metadata.platform === 'win32' ? 'python/python.exe' : 'python/bin/python3']) await access(path.join(resources, file))
  // 仅在实际打包了技能时核对技能目录；无技能安装包不包含该路径。
  if (metadata.bundledSkills.length) await access(path.join(resources, 'site/.harness/skills'))
}
