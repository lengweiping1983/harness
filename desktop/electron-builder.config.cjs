const {readFileSync} = require('node:fs')
const path = require('node:path')
const yaml = require('js-yaml')
const updateSource = require('./update-source.cjs')
module.exports = async function () {
  const {setupCopy} = await import('./setup-copy.mjs')
  const {sealDevelopmentApp, verifyMacBundle} = await import('./mac-signing.mjs')
  const copy = setupCopy()
  const config = yaml.load(readFileSync(path.join(__dirname, 'electron-builder.yml'), 'utf8'))
  // 本机/沙箱构建若 zip 附加工序不可用，可用 HARNESS_MAC_TARGETS=dmg 只打 DMG。
  // 正式发布不设置该变量，保持 dmg + zip（release-assets.mjs 需要 zip 与 blockmap）。
  if (process.env.HARNESS_MAC_TARGETS && config.mac) {
    const targets = process.env.HARNESS_MAC_TARGETS.split(',').map(value => value.trim()).filter(Boolean)
    if (targets.length) config.mac.target = targets
  }
  config.afterPack = sealDevelopmentApp
  config.afterSign = verifyMacBundle
  config.publish = updateSource(process.env, copy)
  config.extraMetadata = {...config.extraMetadata, harnessUpdateSource:config.publish}
  config.mac.extendInfo = {...config.mac.extendInfo, NSMicrophoneUsageDescription:copy('desktop.release.microphoneUsage'), NSCameraUsageDescription:copy('desktop.release.cameraUsage')}
  if (process.env.HARNESS_RELEASE_BUILD === '1') {
    config.forceCodeSigning = true
    if (process.platform === 'darwin') {
      if (!process.env.APPLE_ID || !process.env.APPLE_APP_SPECIFIC_PASSWORD || !process.env.APPLE_TEAM_ID) throw Error(copy('desktop.release.notarizationRequired'))
      config.mac.notarize = true
    }
  }
  return config
}
