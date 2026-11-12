import { t } from '@/i18n'
/**
 * 交付物卡片的数据整形（纯函数）。
 *
 * `present` 工具回传的是**绝对路径**（工具内部做过 resolve，保证文件真的
 * 存在），但 `/api/v1/file` 使用**相对工作区根**的路径。根内路径显式换算，
 * 根外路径保留绝对身份，由呈现层停用预览与下载，不能剥掉斜杠伪装成项目文件。
 *
 * 单独抽成 .ts 有两个原因：
 *   1. 换算是纯逻辑，供文件链接、变更和交付卡片共用；
 *   2. Windows 盘符 + 混合分隔符 + 大小写折叠是最容易写错又最难肉眼验的部分。
 */

import type { DeliverableFile } from '@/types'

/** 工作区可读取的相对路径；绝对路径必须属于声明的根，越界时保持未知。 */
export function workspaceRelativePath(path: string, root: string | null | undefined): string | null {
  const posix = path.trim().replace(/\\/g, '/')
  if (!posix || posix.split('/').some(part => part === '..' || part === '.')) return null
  if (!/^(?:[a-z]:\/|\/)/i.test(posix)) return posix
  let base = root?.trim().replace(/\\/g, '/').replace(/\/+$/, '') || ''
  if (!base && root?.trim() === '/') base = '/'
  if (!/^(?:[a-z]:\/|\/|[a-z]:$)/i.test(base) || base.split('/').some(part => part === '..' || part === '.')) return null
  const prefix = base.endsWith('/') ? base : base + '/'
  const windows = /^[a-z]:/i.test(base) || base.startsWith('//')
  if (!(windows ? posix.toLowerCase().startsWith(prefix.toLowerCase()) : posix.startsWith(prefix))) return null
  return posix.slice(prefix.length) || null
}

/**
 * 把绝对路径换算成相对 ``root`` 的 posix 路径。
 *
 * - 路径不在 ``root`` 之下时保留绝对身份；调用方不能为它生成项目内链接。
 * - 已经在根下：去掉前缀并统一成正斜杠，与 `/api/v1/workspace` 的 ``parents``
 *   面包屑格式一致，面包屑和链接才能互相拼。
 */
export function relativizeToRoot(absPath: string, root: string | null | undefined): string {
  const raw = (absPath ?? '').trim()
  if (!raw) return ''
  const posix = raw.replace(/\\/g, '/')
  return workspaceRelativePath(posix, root) ?? posix
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
