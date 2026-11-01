export type Theme = 'dark' | 'light' | 'system'

export function resolveTheme(theme: Theme, systemDark = window.matchMedia?.('(prefers-color-scheme: dark)')?.matches ?? false): 'dark' | 'light' {
  return theme === 'system' ? systemDark ? 'dark' : 'light' : theme
}

export function applyTheme(theme: Theme): void {
  const resolved = resolveTheme(theme)
  document.documentElement.classList.toggle('dark', resolved === 'dark')
  document.documentElement.style.colorScheme = resolved
  document.documentElement.dataset.theme = theme
  try { localStorage.setItem('harness.ui-theme', theme) } catch { /* Storage can be unavailable. */ }
}
