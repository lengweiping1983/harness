/** Resolve an address only on submission; typing never sends a search request. */
export function browserAddress(input: string): string | null {
  const value = input.trim()
  if (!value || value.length > 8192 || /[\u0000-\u001f\u007f\\]/.test(value)) return null
  const local = /^(localhost|127(?:\.\d{1,3}){3}|\[::1\])(?=[:/?#]|$)/i.test(value)
  const explicit = /^[a-z][a-z\d+.-]*:/i.test(value) && !local && !/^[^\s/:]+:\d+(?:[/?#]|$)/.test(value)
  if (explicit && !/^https?:\/\//i.test(value)) return null
  if (/^[\/]|^[a-z]:/i.test(value) && !explicit) return null
  const hostLike = /^https?:\/\//i.test(value) || !/\s/.test(value) && (local || /^[^/?#]+\.[^/?#]+/.test(value) || /^[^\s/:]+:\d+(?:[/?#]|$)/.test(value))
  if (hostLike) {
    try {
      const url = new URL(/^https?:\/\//i.test(value) ? value : `${local ? 'http' : 'https'}://${value}`)
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || !url.hostname) return null
      return url.href
    } catch {return null}
  }
  return `https://duckduckgo.com/?q=${encodeURIComponent(value)}`
}
