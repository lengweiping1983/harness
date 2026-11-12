const failures = {
  inactive: {code:'browser/dialog-inactive', key:'browser.desktop.dialogInactive'},
  pending: {code:'browser/dialog-pending', key:'browser.desktop.dialogPending'},
  invalid: {code:'browser/dialog-invalid', key:'browser.desktop.dialogInvalid'},
  tabClosed: {code:'browser/tab-closed', key:'browser.desktop.tabClosed'},
  tabLimit: {code:'browser/tab-limit', key:'browser.desktop.tabLimit'},
  navigationBlocked: {code:'browser/dialog-open', key:'browser.desktop.navigationBlocked'},
  invalidSession: {code:'browser/session-invalid', key:'browser.desktop.invalidSession'},
  controlChanged: {code:'browser/control-changed', key:'browser.desktop.controlChanged'},
  invalidOwner: {code:'browser/controller-invalid', key:'browser.desktop.invalidOwner'},
  targetMissing: {code:'browser/target-missing', key:'browser.desktop.targetMissing'},
  bridgeAction: {code:'browser/bridge-action', key:'browser.desktop.bridgeAction'},
  untrusted: {code:'browser/request-untrusted', key:'browser.desktop.untrusted'},
  sessionChanged: {code:'browser/session-changed', key:'browser.desktop.sessionChanged'},
  zoomInvalid: {code:'browser/zoom-invalid', key:'browser.desktop.zoomInvalid'},
  tabMissing: {code:'browser/tab-missing', key:'browser.desktop.tabMissing'},
  actionInvalid: {code:'browser/action-invalid', key:'browser.desktop.actionInvalid'},
  boundsInvalid: {code:'browser/bounds-invalid', key:'browser.desktop.boundsInvalid'},
  findInvalid: {code:'browser/find-invalid', key:'browser.desktop.findInvalid'},
  pageChanged: {code:'browser/desktop-page', key:'browser.desktop.pageChanged'},
  pageFailed: {code:'browser/desktop-page', key:'browser.desktop.pageFailed'},
  pageInitializing: {code:'browser/desktop-page', key:'browser.desktop.pageInitializing'},
  inputChanged: {code:'browser/desktop-page', key:'browser.desktop.inputChanged'},
  viewportTimeout: {code:'browser/desktop-page', key:'browser.desktop.viewportTimeout'},
  viewportPending: {code:'browser/desktop-page', key:'browser.desktop.viewportPending'},
  viewportInvalid: {code:'browser/viewport-invalid', key:'browser.desktop.viewportInvalid'},
  viewportChanged: {code:'browser/desktop-page', key:'browser.desktop.viewportChanged'},
  urlInvalid: {code:'browser/url-invalid', key:'browser.desktop.invalidAddress'},
  permissionInactive: {code:'browser/permission-inactive', key:'browser.desktop.permissionInactive'},
  permissionInvalid: {code:'browser/permission-invalid', key:'browser.desktop.permissionInvalid'},
  systemPermission: {code:'browser/system-permission-required', key:'ui.systemPermission.requestFailed'},
  downloadNameLimit: {code:'browser/download-name-limit', key:'browser.desktop.downloadNameLimit'},
  downloadMissing: {code:'browser/download-missing', key:'browser.desktop.downloadMissing'},
  downloadIncomplete: {code:'browser/download-incomplete', key:'browser.desktop.downloadIncomplete'},
  downloadStopped: {code:'browser/download-stopped', key:'browser.desktop.downloadStopped'},
  downloadAction: {code:'browser/download-action', key:'browser.desktop.downloadAction'},
}

export class NativeBrowserError extends Error {
  constructor(kind, params = {}, options) {
    const failure = failures[kind]
    super(failure.code, options)
    this.code = failure.code
    this.copy = {key:failure.key, params}
  }
}
