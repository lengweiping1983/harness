const panes = {screen:'ScreenCapture', microphone:'Microphone', camera:'Camera', accessibility:'Accessibility'}

/** 系统权限只在功能被显式调用时申请；拒绝不缓存为永久失败。 */
export class SystemPermissions {
  constructor({systemPreferences, desktopCapturer, shell, platform = process.platform}) {
    Object.assign(this, {systemPreferences, desktopCapturer, shell, platform})
    this.pending = new Map()
  }
  status(capability) {
    if (!Object.hasOwn(panes, capability)) throw Error('Invalid system permission')
    if (capability === 'accessibility') return this.platform !== 'darwin' || this.systemPreferences.isTrustedAccessibilityClient(false) ? 'granted' : 'denied'
    return ['darwin','win32'].includes(this.platform) ? this.systemPreferences.getMediaAccessStatus(capability) : 'granted'
  }
  request(capability) {
    if (this.pending.has(capability)) return this.pending.get(capability)
    const operation = Promise.resolve().then(() => this.authorize(capability)).catch(cause => {
      const error = new Error('System permission request failed', {cause})
      error.copy = {key:'ui.systemPermission.requestFailed'}
      throw error
    }).finally(() => this.pending.delete(capability))
    this.pending.set(capability, operation)
    return operation
  }
  async authorize(capability) {
    const status = this.status(capability)
    if (status === 'granted') return {granted:true}
    if (this.platform === 'darwin' && status === 'not-determined') {
      if (capability === 'screen') {
        try {await this.desktopCapturer.getSources({types:['screen'], thumbnailSize:{width:0,height:0}, fetchWindowIcons:false})}
        catch (error) {if (this.status(capability) !== 'denied') throw error}
        if (this.status(capability) === 'granted') return {granted:true}
      } else if (await this.systemPreferences.askForMediaAccess(capability)) return {granted:true}
      return {granted:false, copy:{key:`ui.systemPermission.${capability}.request`}}
    }
    if (this.platform === 'darwin') {
      await this.shell.openExternal(`x-apple.systempreferences:com.apple.preference.security?Privacy_${panes[capability]}`)
    } else if (this.platform === 'win32' && ['microphone','camera'].includes(capability)) {
      await this.shell.openExternal(`ms-settings:privacy-${capability === 'camera' ? 'webcam' : 'microphone'}`)
    } else return {granted:true}
    return {granted:false, copy:{key:`ui.systemPermission.${capability}.settings`}}
  }
}

/** Electron 的异常序列化不保留资源身份，截图失败使用显式回执。 */
export async function screenshotReply(execute, action) {
  try {return await execute()}
  catch (error) {
    console.error('Screenshot failed:', error)
    return {failure:error.copy ?? {key:action === 'finish' ? 'ui.screenshot.cleanupUnknown' : 'ui.screenshot.captureFailed'}}
  }
}
