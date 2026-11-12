import path from 'node:path'
import {access} from 'node:fs/promises'
import {execFile} from 'node:child_process'
import {promisify} from 'node:util'
import {setupCopy} from './setup-copy.mjs'

const executeFile = promisify(execFile)
const execute = (file, args) => executeFile(file, args, {timeout:300000, maxBuffer:1024 * 1024})
function bundlePath(context) {
  return path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
}

/** DMG 内必须是完整封签的应用；开发签名不能冒充证书签名或公证。 */
export async function verifyMacBundle(context, {command = execute, copy = setupCopy()} = {}) {
  if (context.electronPlatformName !== 'darwin') return
  const bundle = bundlePath(context)
  try {
    await access(path.join(bundle, 'Contents/_CodeSignature/CodeResources'))
    await command('/usr/bin/codesign', ['--verify', '--deep', '--strict', bundle])
  } catch (cause) {throw Error(copy('desktop.release.signatureInvalid'), {cause})}
}

/** 构建暂存中的完整应用先封签，避免只留下 Electron 的 linker 签名。 */
export async function sealDevelopmentApp(context, {command = execute, copy = setupCopy()} = {}) {
  if (context.electronPlatformName !== 'darwin' || context.packager.config.forceCodeSigning) return
  try {
    await command('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', '--identifier', context.packager.appInfo.id, bundlePath(context)])
  } catch (cause) {throw Error(copy('desktop.release.signatureInvalid'), {cause})}
  await verifyMacBundle(context, {command, copy})
}
