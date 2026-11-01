import data from './codexFileIcons.json'

// 源图标表用大写条目声明无后缀文件名；专用名称映射仍优先。
const byName: Record<string, string> = {
  ...Object.fromEntries(Object.entries(data.byExtension)
    .filter(([name]) => name !== name.toLowerCase())
    .map(([name, token]) => [name.toLowerCase(), token])),
  ...data.byName,
}
const byExtension: Record<string, string> = data.byExtension
const compound: Record<string, string> = data.byCompoundExtension
const tokens: Record<string, string> = data.tokens
const colors: Record<string, string> = data.colors

/** 文件名优先，再按最长复合后缀匹配；未知类型使用通用文件图标。 */
export function fileIcon(path: string): { token: string; color: string } {
  const name = path.split(/[\\/]/).pop()?.toLowerCase() ?? ''
  let token = Object.hasOwn(byName, name) ? byName[name] : undefined
  const parts = name.split('.')
  for (let index = 1; !token && index < parts.length; index++) {
    const extension = parts.slice(index).join('.')
    token = Object.hasOwn(compound, extension) ? compound[extension] : Object.hasOwn(byExtension, extension) ? byExtension[extension] : undefined
  }
  token ??= 'default'
  return { token, color: colors[tokens[token] ?? ''] ?? 'var(--color-text-muted, currentColor)' }
}
