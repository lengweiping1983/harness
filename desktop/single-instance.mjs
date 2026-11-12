export function claimDesktopInstance(app, getWindow) {
  if (!app.requestSingleInstanceLock()) {app.quit(); return null}
  let pendingFocus = false
  const focus = () => {
    const window = getWindow()
    if (!window || window.isDestroyed()) {pendingFocus = true; return}
    pendingFocus = false
    if (window.isMinimized()) window.restore()
    window.show()
    window.focus()
  }
  app.on('second-instance', focus)
  app.on('activate', focus)
  return () => {if (pendingFocus) focus()}
}
