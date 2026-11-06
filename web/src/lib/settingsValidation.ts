/** 空值与非法值分开处理；不得把空字段转换成零或截断小数。 */
export function parseInteger(raw: string, minimum = 1, maximum = Number.MAX_SAFE_INTEGER): number | undefined {
  if (!raw.trim()) return undefined
  const value = Number(raw)
  return Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : undefined
}

export function validTunnelUrl(raw: string): boolean {
  if (!raw || raw.length > 2048 || /\s/.test(raw)) return false
  try {
    const url = new URL(raw)
    return url.protocol === 'https:' && url.hostname.includes('.') && !url.username && !url.password
      && !url.search && !url.hash && url.pathname === '/' && (!url.port || url.port === '443')
  } catch { return false }
}
