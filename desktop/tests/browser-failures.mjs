import {NativeBrowserError} from '../browser-errors.mjs'

export const browserFailureCases = [
  ["inactive", {}],
  ["pending", {}],
  ["invalid", {}],
  ["tabClosed", {}],
  ["tabLimit", {"count": 12}],
  ["navigationBlocked", {}],
  ["invalidSession", {}],
  ["controlChanged", {}],
  ["invalidOwner", {}],
  ["targetMissing", {}],
  ["bridgeAction", {}],
  ["untrusted", {}],
  ["sessionChanged", {}],
  ["zoomInvalid", {}],
  ["tabMissing", {}],
  ["actionInvalid", {}],
  ["boundsInvalid", {}],
  ["findInvalid", {"count": 1000}],
  ["pageChanged", {}],
  ["pageFailed", {}],
  ["pageInitializing", {}],
  ["inputChanged", {}],
  ["viewportTimeout", {}],
  ["viewportPending", {}],
  ["viewportInvalid", {}],
  ["viewportChanged", {}],
  ["urlInvalid", {}],
  ["permissionInactive", {}],
  ["permissionInvalid", {}],
  ["downloadNameLimit", {}],
  ["downloadMissing", {}],
  ["downloadIncomplete", {}],
  ["downloadStopped", {}],
  ["downloadAction", {}],
]

export const browserFailures = () => browserFailureCases.map(([kind,params]) => {
  const error = new NativeBrowserError(kind,params)
  return {kind,code:error.code,copy:error.copy}
})
