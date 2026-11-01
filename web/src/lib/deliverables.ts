import { t } from '@/i18n'
/**
 * 交付物卡片的数据整形（纯函数）。
 *
 * `present` 工具回传的是**绝对路径**（工具内部做过 resolve，保证文件真的
 * 存在），但 `/api/v1/file` 只接受**相对工作区根**的路径，并且 `_safe_join`
 * 会把逃出根的路径 403掉。两者之间必须有一层显式换算 —— 之前少了这一步的
 * 直接后果是：卡片点开全是 403，而且错误信息只有后端日志能看到。
 *
 * 单独抽成 .ts 有两个原因：
 *   1. 换算是纯逻辑，.tsx 测不到（测试环境不含 tsx）；
 *   2. Windows 盘符 + 混合分隔符 + 大小写折叠是最容易写错又最难肉眼验的部分。
 */

import type { DeliverableFile } from '@/types'
import { normalizePath } from './workspaces'

/**
 * 把绝对路径换算成相对 ``root`` 的 posix 路径。
 *
 * - ``root`` 为空 / 路径不在 ``root`` 之下：退回文件名（后端会明确报"路径超出
 *   工作区范围"，比给一个看起来能点、点开 403 的链接诚实）；
 * - 已经在根下：去掉前缀并统一成正斜杠，与 `/api/v1/workspace` 的 ``parents``
 *   面包屑格式一致，面包屑和链接才能互相拼。
 */
export function relativizeToRoot(absPath: string, root: string | null | undefined): string {
  const raw = (absPath ?? '').trim()
  if (!raw) return ''
  const posix = raw.replace(/\\/g, '/')
  const nPath = normalizePath(posix)
  const nRoot = normalizePath(root)
  if (!nRoot) return posix.replace(/^\/+/, '')
  if (nPath === nRoot) return ''
  const prefix = nRoot.endsWith('/') ? nRoot : nRoot + '/'
  // 不在根下就退回原样的相对形态：宁可让后端明确报"路径超出工作区范围"，
  // 也不要给一个看起来能点、点开却是 403 的链接。
  if (!nPath.startsWith(prefix)) return posix.replace(/^\/+/, '')
  // normalizePath 只折叠大小写、不改长度，所以按前缀长度切原始串
  // 就能保留原始大小写（"README.md" 不能被切成 "readme.md"）。
  return posix.slice(prefix.length)
}

export interface DeliverableGroup {
  /** 首层目录名；顶层文件归到 ``''`` 组。 */
  dir: string
  label: string
  files: DeliverableFile[]
}

/**
 * 按首层目录分组，组内保持声明顺序。
 *
 * 一轮里常常同时产出 `src/a.ts`、`src/b.ts`、`README.md`；平铺 20 行看不出
 * "改了什么模块"，分组后一眼就能定位。组序按**首个文件出现的位置**，
 * 而不是字母序 —— 声明顺序本身就是模型的叙述顺序。
 */
export function groupDeliverables(
  files: DeliverableFile[],
  root?: string | null,
): DeliverableGroup[] {
  const groups = new Map<string, DeliverableGroup>()
  for (const file of files) {
    // 必须先换算成相对路径再取首层目录：``present`` 回的是绝对路径，
    // 直接切首段会得到盘符 ``D:`` 当成"目录名"。
    const relative = relativizeToRoot(file.path, root)
      .replace(/\\/g, '/')
      .replace(/^\/+/, '')
    const cut = relative.indexOf('/')
    const dir = cut === -1 ? '' : relative.slice(0, cut)
    const label = dir ? dir.split('/')[0]! : t("ui.lib.deliverables.42ee2863b6d7")
    const bucket = groups.get(dir)
    if (bucket) bucket.files.push(file)
    else groups.set(dir, { dir, label, files: [file] })
  }
  return [...groups.values()]
}

/** 交付物总字节数；缺 size 的按 0 计（后端 stat 失败时会出现）。 */
export function totalBytes(files: DeliverableFile[]): number {
  return files.reduce((sum, f) => sum + (Number(f.size_bytes) || 0), 0)
}

/** 摘要文案："3 个文件 · 12.4 KB"。 */
export function summarizeDeliverables(files: DeliverableFile[]): string {
  if (!files.length) return t("ui.lib.deliverables.bb4b39e92330")
  const bytes = totalBytes(files)
  const parts = [t("ui.lib.deliverables.85e151fe4968", {p0: (files.length)})]
  if (bytes > 0) parts.push(formatBytesLite(bytes))
  return parts.join(' · ')
}

function formatBytesLite(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`
}
