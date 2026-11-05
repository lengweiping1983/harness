function updateLabel({text, state, progress, phase, received = 0, total}) {
  if (state === 'downloading') {
    const key = {connecting:'updateConnectingMenu', verifying:'updateVerifyingMenu', preparing:'updatePreparingMenu'}[phase]
    if (key) return text(key)
    if (Number.isFinite(total) && total > 0) {
      const size = bytes => {
        const index = Math.min(3, Math.floor(Math.log(Math.max(1, bytes)) / Math.log(1024)))
        return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${['B','KB','MB','GB'][index]}`
      }
      return text('updateTransferMenu').replace('{percent}', Math.max(0, Math.min(100, progress || 0)).toFixed(1)).replace('{received}', size(received)).replace('{total}', size(total))
    }
    if (Number.isFinite(progress)) return `${text('updateDownloadingMenu')} ${progress}%`
  }
  return text({checking:'updateCheckingMenu', available:'updateAvailableMenu', downloading:'updateDownloadingMenu', downloaded:'updateReadyMenu'}[state] || 'checkUpdates')
}

/** 复用原生菜单项，下载通知不替换 AppKit 正在展示的菜单树。 */
export function refreshUpdateMenu(menu, snapshot, text) {
  const item = menu?.getMenuItemById('harness-updates')
  if (!item) return
  item.label = updateLabel({text, ...snapshot})
  item.enabled = !['checking','downloading'].includes(snapshot.state)
}

export function updateDockProgress({state, phase, progress}) {
  if (state !== 'downloading') return -1
  return ['connecting','verifying','preparing'].includes(phase) || !Number.isFinite(progress) ? 2 : progress / 100
}

export function applicationMenu({platform = process.platform, text, command, ready = true, updateState = 'idle', updateProgress = null, updatePhase = null, updateReceived = 0, updateTotal = null}) {
  const item = (key, action, accelerator) => ({label:text(key), accelerator, enabled:ready, click:() => command(action)})
  const role = (key, value) => ({label:text(key), role:value})
  const settings = item('settings', 'settings', 'CommandOrControl+,')
  const about = {...item('about', 'about'), enabled:true}
  const updates = {...item('checkUpdates', 'updates'), id:'harness-updates', label:updateLabel({text, state:updateState, progress:updateProgress, phase:updatePhase, received:updateReceived, total:updateTotal})}
  updates.enabled = !['checking','downloading'].includes(updateState)
  return [
    ...(platform === 'darwin' ? [{label:'Harness', submenu:[about, settings, {type:'separator'}, role('hide', 'hide'), role('hideOthers', 'hideOthers'), role('unhide', 'unhide'), {type:'separator'}, role('quit', 'quit')]}] : []),
    {label:text('file'), submenu:[item('newChat', 'new-chat', 'CommandOrControl+Shift+U'), item('openProject', 'open-project', 'CommandOrControl+Shift+O'), {type:'separator'}, item('screenshot', 'screenshot', 'CommandOrControl+Shift+S'), item('screenshotHidden', 'screenshot-hidden', 'CommandOrControl+Shift+H'), ...(platform === 'darwin' ? [] : [{type:'separator'}, settings, {type:'separator'}, role('quit', 'quit')])]},
    {label:text('edit'), submenu:[role('undo', 'undo'), role('redo', 'redo'), {type:'separator'}, role('cut', 'cut'), role('copy', 'copy'), role('paste', 'paste'), role('selectAll', 'selectAll')]},
    {label:text('view'), submenu:[item('commands', 'commands', 'CommandOrControl+K'), item('sidebar', 'sidebar', 'CommandOrControl+B'), item('inspector', 'inspector', 'CommandOrControl+J'), {type:'separator'}, item('files', 'files'), item('terminal', 'terminal'), item('browser', 'browser'), {type:'separator'}, role('fullscreen', 'togglefullscreen')]},
    {label:text('window'), submenu:[role('minimize', 'minimize'), ...(platform === 'darwin' ? [role('zoom', 'zoom'), {type:'separator'}, role('front', 'front')] : [])]},
    {label:text('help'), submenu:[updates, item('shortcuts', 'shortcuts'), ...(platform === 'darwin' ? [] : [about])]},
  ]
}

/** 只有主文档替换才撤销页面菜单的就绪状态，内嵌预览加载不影响工作台。 */
export function attachMenuLifecycle(window, installMenu) {
  // closed 触发时窗口已销毁，清理使用注册时保存的页面引用。
  const contents = window.webContents
  const navigating = details => {
    if (details.isMainFrame && !details.isSameDocument) installMenu(false)
  }
  const crashed = () => installMenu(false)
  contents.on('did-start-navigation', navigating)
  contents.on('render-process-gone', crashed)
  window.once('closed', () => {
    contents.off('did-start-navigation', navigating)
    contents.off('render-process-gone', crashed)
  })
}

/** 使用进程内默认值阻止 AppKit 自动添加第二个全屏入口。 */
export function configureMenuDefaults(preferences, platform = process.platform) {
  if (platform === 'darwin') preferences.registerDefaults({NSFullScreenMenuItemEverywhere:false})
}
