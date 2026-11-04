/** 仅向可信本机应用页面授予所需能力，第三方网页仍使用独立权限会话。 */
export function applicationPermission({webContents, permission, requestingOrigin, details = {}, origin, window, voiceWindow}) {
  let requestOrigin
  try {requestOrigin = new URL(requestingOrigin).origin} catch {return false}
  if (!webContents || webContents.isDestroyed() || requestOrigin !== origin) return false
  if (details.requestingUrl) {
    try {if (new URL(details.requestingUrl).origin !== origin) return false} catch {return false}
  }
  if (permission === 'clipboard-sanitized-write') {
    // 不授予剪贴板读取，也不授予子框架或语音浮窗写入。
    if (webContents !== window?.webContents || details.isMainFrame === false) return false
    try {return new URL(webContents.mainFrame.url).origin === origin} catch {return false}
  }
  if (permission !== 'media' || ![window?.webContents, voiceWindow?.webContents].includes(webContents)) return false
  const mediaTypes = details.mediaTypes || (details.mediaType ? [details.mediaType] : [])
  return mediaTypes.length === 1 && mediaTypes[0] === 'audio'
}

export function attachApplicationPermissions(session, getContext) {
  session.setPermissionCheckHandler((webContents, permission, requestingOrigin, details) =>
    applicationPermission({...getContext(), webContents, permission, requestingOrigin, details}))
  session.setPermissionRequestHandler((webContents, permission, callback, details) =>
    callback(applicationPermission({...getContext(), webContents, permission, requestingOrigin:details?.requestingUrl || '', details})))
}
