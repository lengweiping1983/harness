/**
 * 链接 / 媒体地址解析。
 *
 * 链接解析规则：
 *
 * 1. **协议白名单** —— 只有 `http/https/mailto` 能直接跳；`javascript:`、
 *    `data:text/html`、`vbscript:` 一律降级成纯文本。这是 Markdown 里
 *    唯一的真实 XSS 面。
 * 2. **工作区路径** —— 模型输出的相对路径要转成 `/api/v1/file?path=...`，
 *    否则 `<img src="shots/a.png">` 在浏览器里 404（相对的是站点根，不是工作区）。
 * 3. **行号片段** —— `[见这里](src/a.py#L12-L20)` 要能落到
 *    `/api/v1/file?path=src/a.py#L12`，让编辑器/查看器能高亮。
 */

import { getFileRoot } from './fileRoot'
import { relativizeToRoot, workspaceRelativePath } from './deliverables'
import { validLineRange } from './fileNavigation'

const SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:'])
/** 图片额外允许 data:（内联 base64）与 blob:（预览生成的 objectURL）。 */
const IMAGE_EXTRA_PROTOCOLS = new Set(['data:', 'blob:'])

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|avif|bmp|svg|ico)$/i
const VIDEO_EXT = /\.(mp4|webm|mov|m4v|ogv|mkv)$/i
const AUDIO_EXT = /\.(mp3|wav|ogg|m4a|aac|flac|oga)$/i
const PDF_EXT = /\.pdf$/i

export type MediaKind = 'image' | 'video' | 'audio' | 'pdf' | 'file' | 'external' | 'anchor' | 'unsafe'

export interface ResolvedLink {
  href: string
  kind: MediaKind
  /** 工作区相对路径（kind 为 image/video/audio/pdf/file 时有值）。 */
  workspacePath?: string
  /** API 地址中指定的读取根；null 表示服务端默认根，不能替换成当前对话根。 */
  workspaceRoot?: string | null
  download?: boolean
  lineRange?: { start: number; end?: number }
  external: boolean
}

export interface FileLinkSource { root: string | null; directory: string }

/** Resolve document-relative paths without hiding attempts to walk above the root. */
function documentPath(directory: string, path: string): string {
  const parts: string[] = []
  for (const part of `${directory}/${path}`.replace(/\\/g, '/').split('/')) {
    if (!part || part === '.') continue
    if (part === '..' && parts.length && parts[parts.length - 1] !== '..') parts.pop()
    else parts.push(part)
  }
  return parts.join('/')
}

export function fileUrl(
  path: string,
  opts: { download?: boolean; root?: string | null } = {},
): string {
  const qs = new URLSearchParams({ path })
  if (opts.download) qs.set('download', '1')
  const root = opts.root !== undefined ? opts.root : getFileRoot()
  if (root) qs.set('root', root)
  return `/api/v1/file?${qs.toString()}`
}

export function isHtmlPath(target: string): boolean {
  return /\.(html?|xhtml)$/i.test(target.split('#')[0]!.split('?')[0]!)
}

/**
 * 网页预览地址：目录结构落在路径里，页面内的相对资源才能解析回工作区文件。
 * 工作区根本身没有目录名可用，用 `__root__` 占位。
 */
export function previewUrl(
  path: string,
  opts: { root?: string | null } = {},
): string {
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean)
  const entry = parts.pop() ?? ''
  const folder = parts.join('/')
  const qs = new URLSearchParams()
  const root = opts.root !== undefined ? opts.root : getFileRoot()
  if (root) qs.set('root', root)
  const query = qs.size ? `?${qs.toString()}` : ''
  return `/api/v1/preview/${encodeURIComponent(folder || '__root__')}/${encodeURIComponent(entry)}${query}`
}

/** 从 `#L12` / `#L12-L20` 解析行号区间。 */
export function parseLineFragment(hash: string): { start: number; end?: number } | undefined {
  const m = /^#L(\d+)(?:-L?(\d+))?$/.exec(hash.trim())
  if (!m) return undefined
  const start = Number(m[1])
  const end = m[2] ? Number(m[2]) : undefined
  return validLineRange(start, end)
}

/** 看起来像工作区相对路径（而非 URL / 锚点）。 */
export function looksLikePath(target: string): boolean {
  if (!target) return false
  if (/^[a-z][a-z0-9+.-]*:/i.test(target)) return false // 有协议
  if (target.startsWith('#')) return false
  if (target.startsWith('//')) return false
  if (target.startsWith('/')) return true // 绝对文件路径须继续核对实际工作区归属
  const path = target.split(/[?#]/, 1)[0] ?? ''
  return /[\\/]/.test(path) || /\.[A-Za-z0-9]{1,6}$/.test(path)
}

export function guessKind(target: string, isImageContext = false): MediaKind {
  const clean = target.split('#')[0]!.split('?')[0]!
  if (IMAGE_EXT.test(clean)) return 'image'
  if (VIDEO_EXT.test(clean)) return 'video'
  if (AUDIO_EXT.test(clean)) return 'audio'
  if (PDF_EXT.test(clean)) return 'pdf'
  return isImageContext ? 'image' : 'file'
}

export function isSafeExternal(url: string, allowData = false): boolean {
  try {
    const parsed = new URL(url, typeof window === 'undefined' ? 'https://localhost/' : window.location.href)
    if (allowData && IMAGE_EXTRA_PROTOCOLS.has(parsed.protocol)) return true
    return SAFE_PROTOCOLS.has(parsed.protocol)
  } catch {
    return false
  }
}

/**
 * 把 Markdown 里的链接目标解析成可渲染地址。
 * @param isImageContext `<img src>` 的上下文下，无扩展名也当媒体处理。
 */
export function resolveLink(rawTarget: string, isImageContext = false, source?: FileLinkSource): ResolvedLink {
  const root = source ? source.root : getFileRoot()
  let target = (rawTarget ?? '').trim()
  if (!target) return { href: '', kind: 'anchor', external: false }

  if (target.startsWith('#')) {
    const range = parseLineFragment(target)
    return { href: target, kind: 'anchor', external: false, ...(range ? { lineRange: range } : {}) }
  }

  // 盘符不是 URL 协议。兼容 Agent 常用的“绝对路径:行号”文件引用。
  const lineSuffix = /^(.*\.[A-Za-z0-9]+):(\d+)$/.exec(target)
  if (lineSuffix && !target.startsWith('/api/v1/') && (/^[A-Za-z]:[\\/]/.test(target) || !/^[a-z][a-z0-9+.-]*:/i.test(target))) target = `${lineSuffix[1]}#L${lineSuffix[2]}`
  if (/^[A-Za-z]:[\\/]/.test(target)) {
    const [absolute = '', fragment] = target.split('#')
    let decoded = absolute
    try { decoded = decodeURIComponent(absolute) } catch { /* 畸形百分号保留为文件名，由服务端确认。 */ }
    if (workspaceRelativePath(decoded, root) === null) return { href: '#', kind: 'unsafe', external: false }
    const path = relativizeToRoot(decoded, root)
    const hash = fragment ? `#${fragment}` : ''
    return { href: fileUrl(path, { root }) + hash, kind: guessKind(path, isImageContext), workspacePath: path, workspaceRoot: root, lineRange: parseLineFragment(hash), external: false }
  }

  // 明确的协议
  const protocolMatch = /^([a-z][a-z0-9+.-]*):/i.exec(target)
  if (protocolMatch) {
    const protocol = `${protocolMatch[1]!.toLowerCase()}:`
    const allowData = isImageContext && IMAGE_EXTRA_PROTOCOLS.has(protocol)
    if (SAFE_PROTOCOLS.has(protocol) || allowData) {
      const kind = isImageContext ? 'image' : guessKind(target)
      return { href: target, kind, external: true }
    }
    // javascript: / vbscript: / 未知协议 —— 交回调用方当纯文本
    return { href: '#', kind: 'unsafe', external: false }
  }

  if (target.startsWith('//')) {
    // 协议相对 URL
    return { href: `https:${target}`, kind: isImageContext ? 'image' : 'file', external: true }
  }

  // 站内绝对地址（/api/v1/...）—— 后端自己吐出来的最终形态，直接放行。
  // 否则会被当成工作区路径改写成 /api/v1/file?path=api/attachments/xxx，
  // 附件图片 / 音视频一律 404。
  if (target.startsWith('/api/v1/')) {
    if (/^\/api\/v1\/file(?:\?|#|$)/.test(target)) {
      const url = new URL(target, 'https://localhost/')
      const path = url.searchParams.get('path')
      if (path) return {
        href: target, kind: guessKind(path, isImageContext), external: false,
        workspacePath: path, workspaceRoot: url.searchParams.get('root') || null,
        lineRange: parseLineFragment(url.hash), download: url.searchParams.get('download') === '1',
      }
    }
    return { href: target, kind: guessKind(target, isImageContext), external: false }
  }

  if (!looksLikePath(target) && !source) {
    return { href: target, kind: 'anchor', external: false }
  }

  // 工作区路径：剥掉 query / hash，hash 里的行号单独留
  const [pathPart = '', ...restParts] = target.split('#')
  let rawPath = pathPart!.split('?')[0]!
  try { rawPath = decodeURIComponent(rawPath) } catch { /* 保留畸形转义；服务端仍验证路径。 */ }
  if (rawPath.startsWith('/') && workspaceRelativePath(rawPath, root) === null) return { href: '#', kind: 'unsafe', external: false }
  const path = rawPath.startsWith('/') && root ? relativizeToRoot(rawPath, root)
    : source && !rawPath.startsWith('/') ? documentPath(source.directory, rawPath) : rawPath.replace(/^\/+/, '')
  if (!path) return { href: target, kind: 'anchor', external: false }
  const hash = restParts.length ? `#${restParts.join('#')}` : ''
  const lineRange = hash ? parseLineFragment(hash) : undefined
  const kind = guessKind(path, isImageContext)
  return {
    href: `${fileUrl(path, { root })}${hash}`,
    kind,
    workspacePath: path,
    workspaceRoot: root,
    ...(lineRange ? { lineRange } : {}),
    external: false,
  }
}

/** 外部链接统一走 noopener，防 window.opener 劫持。 */
export function externalAttrs(): Record<string, string> {
  return { target: '_blank', rel: 'noopener noreferrer nofollow' }
}

const TEXT_EXT =
  /\.(txt|md|markdown|json|jsonc|ya?ml|toml|ini|cfg|conf|env|log|csv|tsv|xml|html?|css|scss|less|py|js|jsx|ts|tsx|mjs|cjs|go|rs|java|kt|kts|swift|c|h|cc|cpp|hpp|cs|rb|php|sh|bash|zsh|fish|ps1|bat|psm1|sql|graphql|gql|vue|svelte|astro|dockerfile|makefile|gitignore|lock)$/i

export function isTextFile(path: string): boolean {
  const name = path.split(/[\\/]/).pop() ?? path
  if (TEXT_EXT.test(path)) return true
  if (/^(Dockerfile|Makefile|README|LICENSE|CHANGELOG|CONTRIBUTING)$/i.test(name)) return true
  return /^\.[a-z0-9]+$/i.test(name)
}

export function isImagePath(path: string): boolean {
  return IMAGE_EXT.test(path.split('?')[0] ?? '')
}

export function isVideoPath(path: string): boolean {
  return VIDEO_EXT.test(path.split('?')[0] ?? '')
}

export function isAudioPath(path: string): boolean {
  return AUDIO_EXT.test(path.split('?')[0] ?? '')
}
