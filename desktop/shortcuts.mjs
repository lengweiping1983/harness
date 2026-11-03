/** 原生菜单加速键同样遵守组合输入和长按保护。 */
export function attachMenuShortcutGuard(contents) {
  const guard = (_event, input) => contents.setIgnoreMenuShortcuts(Boolean(input.isComposing || input.isAutoRepeat))
  contents.on('before-input-event', guard)
  contents.once('destroyed', () => contents.off('before-input-event', guard))
}

export function browserShortcut(input, platform = process.platform) {
  if (input.type !== 'keyDown' || input.alt || input.isComposing) return null
  const key = input.key.toLowerCase()
  if (key === 'escape' && !input.control && !input.meta && !input.shift) return {action:'find_close'}
  const primary = platform === 'darwin' ? input.meta && !input.control : input.control && !input.meta
  if (input.control && !input.meta && key === 'tab') return {action:'cycle_tab', offset:input.shift ? -1 : 1}
  if (!primary) return null
  if (!input.shift && /^[1-9]$/.test(key)) return {action:'index_tab', index:Number(key) - 1}
  if (['+', '='].includes(key)) return {action:'zoom', direction:1}
  if (key === '-' && !input.shift) return {action:'zoom', direction:-1}
  if (key === '0' && !input.shift) return {action:'zoom', direction:0}
  if (key === 'g') return {action:'find_next', forward:!input.shift}
  if (key === 't' && input.shift) return {action:'reopen_tab'}
  if (input.shift) return null
  const action = {l:'focus_address', f:'find_open', t:'new_tab', w:'close_current_tab', r:'reload'}[key]
  return action ? {action} : null
}
