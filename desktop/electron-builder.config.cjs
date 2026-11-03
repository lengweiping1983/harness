const {readFileSync} = require('node:fs')
const path = require('node:path')
const yaml = require('js-yaml')
const updateSource = require('./update-source.cjs')
module.exports = async function () {
  const {setupCopy} = await import('./setup-copy.mjs')
  const copy = setupCopy()
  const config = yaml.load(readFileSync(path.join(__dirname, 'electron-builder.yml'), 'utf8'))
  config.publish = updateSource(process.env, copy)
  config.extraMetadata = {...config.extraMetadata, harnessUpdateSource:config.publish}
  config.mac.extendInfo = {...config.mac.extendInfo, NSMicrophoneUsageDescription:copy('desktop.release.microphoneUsage')}
  if (process.env.HARNESS_RELEASE_BUILD === '1') {
    config.forceCodeSigning = true
    if (process.platform === 'darwin') {
      if (!process.env.APPLE_ID || !process.env.APPLE_APP_SPECIFIC_PASSWORD || !process.env.APPLE_TEAM_ID) throw Error(copy('desktop.release.notarizationRequired'))
      config.mac.notarize = true
    }
  }
  return config
}
