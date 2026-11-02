export function applicationMenu({platform = process.platform, text, command, ready = true, updateState = 'idle', updateProgress = null}) {
  const item = (key, action, accelerator) => ({label:text(key), accelerator, enabled:ready, click:() => command(action)})
  const role = (key, value) => ({label:text(key), role:value})
  const settings = item('settings', 'settings', 'CommandOrControl+,')
  const about = item('about', 'about')
  const updates = item({checking:'updateCheckingMenu', available:'updateAvailableMenu', downloading:'updateDownloadingMenu', downloaded:'updateReadyMenu'}[updateState] || 'checkUpdates', 'updates')
  if (updateState === 'downloading' && Number.isFinite(updateProgress)) updates.label += ` ${updateProgress}%`
  updates.enabled = ready && !['checking','downloading'].includes(updateState)
  return [
    ...(platform === 'darwin' ? [{label:'Harness', submenu:[about, settings, {type:'separator'}, role('hide', 'hide'), role('hideOthers', 'hideOthers'), role('unhide', 'unhide'), {type:'separator'}, role('quit', 'quit')]}] : []),
    {label:text('file'), submenu:[item('newChat', 'new-chat', 'CommandOrControl+Shift+U'), item('openProject', 'open-project', 'CommandOrControl+Shift+O'), ...(platform === 'darwin' ? [] : [settings, {type:'separator'}, role('quit', 'quit')])]},
    {label:text('edit'), submenu:[role('undo', 'undo'), role('redo', 'redo'), {type:'separator'}, role('cut', 'cut'), role('copy', 'copy'), role('paste', 'paste'), role('selectAll', 'selectAll')]},
    {label:text('view'), submenu:[item('commands', 'commands', 'CommandOrControl+K'), item('sidebar', 'sidebar', 'CommandOrControl+B'), item('inspector', 'inspector', 'CommandOrControl+J'), {type:'separator'}, item('files', 'files'), item('terminal', 'terminal'), item('browser', 'browser'), {type:'separator'}, role('fullscreen', 'togglefullscreen')]},
    {label:text('window'), submenu:[role('minimize', 'minimize'), ...(platform === 'darwin' ? [role('zoom', 'zoom'), {type:'separator'}, role('front', 'front')] : [])]},
    {label:text('help'), submenu:[updates, item('shortcuts', 'shortcuts'), ...(platform === 'darwin' ? [] : [about])]},
  ]
}
