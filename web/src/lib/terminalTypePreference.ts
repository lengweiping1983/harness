const preferenceKey = 'harness.terminal-type.v1'
export function preferredTerminalType() {
  try {return localStorage.getItem(preferenceKey) || ''} catch {return ''}
}
export function rememberTerminalType(value: string) {
  try {localStorage.setItem(preferenceKey, value)} catch { /* Storage may be unavailable. */ }
}
