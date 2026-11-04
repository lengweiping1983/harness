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
