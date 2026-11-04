/** 先确认主窗口是否可离开，再关闭后端；取消离开时保留全部服务。 */
export function desktopQuit({app, getWindow, cleanup, onError, beforeQuit = async () => {}}) {
  let ready = false
  let closing = false
  let shutdown = null
  app.on('before-quit', event => {
    if (ready) return
    event.preventDefault()
    const window = getWindow()
    if (window && !window.isDestroyed()) {
      if (!closing) {closing = true; window.close()}
      return
    }
    if (!shutdown) shutdown = Promise.resolve().then(cleanup)
      .then(() => false, error => {onError(error); return true})
      .then(cleanupFailed => beforeQuit({cleanupFailed}))
      .catch(onError)
      .finally(() => {ready = true; app.quit()})
  })
  return {
    attach(window, confirm) {
      window.webContents.on('will-prevent-unload', event => {
        closing = false
        if (confirm()) event.preventDefault()
      })
    },
  }
}
