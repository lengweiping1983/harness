/** Discover local development addresses without treating arbitrary output as navigation. */
export function localPreviewUrls(output: string): string[] {
  const text = output.slice(-32768).replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
  const found = new Set<string>()
  for (const match of text.matchAll(/https?:\/\/[^\s<>"'`\x00-\x1f]+/gi)) {
    try {
      const url = new URL(match[0].replace(/[),.;]+$/, ''))
      if (url.username || url.password || !['localhost', '127.0.0.1', '[::1]', '0.0.0.0', '[::]'].includes(url.hostname)) continue
      if (url.hostname === '0.0.0.0') url.hostname = '127.0.0.1'
      if (url.hostname === '[::]') url.hostname = '[::1]'
      found.add(url.href)
      if (found.size === 5) break
    } catch { /* Partial output and invalid addresses are not links. */ }
  }
  return [...found]
}
