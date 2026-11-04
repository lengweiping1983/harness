/** 对话框在呈现时消费当前语言；下载与安装不由语言切换触发。 */
export function updateDialogOptions(snapshot, managed, text) {
  if (snapshot.state === 'downloaded') return {
    type:'info', title:'Harness', message:text[managed ? 'updateMacDownloaded' : 'updateDownloaded'], detail:snapshot.version || '',
    buttons:[text.quitInstall, text.later], defaultId:1, cancelId:1,
  }
  const detail = snapshot.mode === 'installer'
    ? text[managed ? 'updateMacInstallerDetail' : 'updateInstallerDetail'].replace('{version}', snapshot.version)
    : snapshot.version
  return {type:'info', title:'Harness', message:text.updateAvailable, detail, buttons:[text.downloadUpdate, text.later], defaultId:0, cancelId:1}
}

/** 失败说明与操作一起生成，重试仅由用户显式选择，不重放安装副作用。 */
export function updateFailureOptions(error, text, {repository, networkSettings = false, networkKind = null} = {}) {
  const keys = ['updateInvalidRelease','updateMetadataTooLarge','updateHttpFailure','updateIntegrityFailed','updateInstallLocation','updateCurrentSignatureInvalid',
    'updateNetworkDns','updateNetworkProxy','updateNetworkTls','updateNetworkOffline','updateNetworkTimeout','updateNetworkConnection']
  const key = keys.includes(error.menuKey) ? error.menuKey : networkKind ? `updateNetwork${networkKind}` : ['ERR_UPDATER_CHANNEL_FILE_NOT_FOUND','ERR_UPDATER_LATEST_VERSION_NOT_FOUND'].includes(error.code) ? 'updateSourceUnavailable' : 'updateFailed'
  const params = error.params || {}
  const message = text[key].replace(/\{(\w+)\}/g, (original, name) => Object.hasOwn(params, name) ? params[name] : original)
  const network = key.startsWith('updateNetwork')
  const actions = [...(network ? ['retry'] : []), ...(network && networkSettings ? ['networkSettings'] : []), ...(repository?.provider === 'github' ? ['releases'] : []), 'close']
  const labels = {retry:'updateRetry', networkSettings:'updateNetworkSettings', releases:'openReleasePage', close:'ok'}
  return {actions, options:{type:'warning', title:'Harness', message,
    detail:network && params.host ? text.updateNetworkDetail.replace('{host}', params.host).replace('{diagnostic}', error.cause?.message || error.message || '') : error.cause?.message || error.message || '', buttons:actions.map(action => text[labels[action]]), defaultId:actions.length - 1, cancelId:actions.length - 1}}
}
