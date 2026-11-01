import {NativeBrowserError} from './browser-errors.mjs'

export async function beginNativeInput(automation, sessionId, snapshot, confirm = () => true) {
  const state = await snapshot()
  if (!confirm(state)) throw new NativeBrowserError('inputChanged')
  automation.add(sessionId)
  return state
}
