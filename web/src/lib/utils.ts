import { t, getLocale } from '@/i18n'
import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/* ============================ 数值 / 时间 ============================ */

export function formatNumber(value: unknown): string {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return '—'
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(n % 1_000 === 0 ? 0 : 1)}k`
  return String(n)
}

export function formatTokens(value: unknown): string {
  const n = Number(value ?? 0)
  return Number.isFinite(n) ? n.toLocaleString(getLocale()) : '0'
}

export function formatBytes(bytes: unknown, unit?: 'KiB'): string {
  const n = Number(bytes ?? 0)
  if (!Number.isFinite(n) || n < 0) return '—'
  if (unit === 'KiB') return `${(n / 1024).toLocaleString(getLocale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 })} KiB`
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = n / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i += 1
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`
}

/** 轮次、执行任务和生成进度共用整秒经过时间；不改变工具耗时的毫秒精度。 */
export function formatElapsedDuration(milliseconds: number): string | null {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return null
  const seconds = Math.floor(milliseconds / 1000)
  return seconds >= 3600
    ? t('ui.turnElapsed.hours', { hours: Math.floor(seconds / 3600), minutes: Math.floor(seconds / 60) % 60, seconds: seconds % 60 })
    : seconds >= 60 ? t('ui.turnElapsed.minutes', { minutes: Math.floor(seconds / 60), seconds: seconds % 60 })
      : t('ui.turnElapsed.seconds', { seconds })
}

export function formatDuration(ms: unknown): string {
  const n = Number(ms ?? 0)
  if (!Number.isFinite(n) || n < 0) return '—'
  if (n < 1000) return `${Math.round(n)}ms`
  if (n < 60_000) return `${(n / 1000).toFixed(n < 10_000 ? 2 : 1)}s`
  const totalSeconds = Math.round(n / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes < 60) return `${minutes}m ${seconds}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

/** 后端时间戳是毫秒 epoch；容错到秒级 / ISO 字符串。 */
export function toDate(value: unknown): Date | null {
  if (value == null) return null
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return null
    // 10 位是秒，13 位是毫秒。
    const date = new Date(value < 1e11 ? value * 1000 : value)
    return Number.isFinite(date.getTime()) ? date : null
  }
  if (typeof value === 'string') {
    const num = Number(value)
    if (Number.isFinite(num) && value.trim() !== '') return toDate(num)
    const d = new Date(value)
    return Number.isNaN(d.getTime()) ? null : d
  }
  return null
}

export function formatRelativeTime(value: unknown): string {
  const d = toDate(value)
  if (!d) return '—'
  const diff = Date.now() - d.getTime()
  if (diff < 0) return d.toLocaleString(getLocale())
  const s = Math.floor(diff / 1000)
  if (s < 60) return t("ui.lib.utils.de6785d99e02")
  const m = Math.floor(s / 60)
  if (m < 60) return t(m === 1 ? "ui.time.oneMinuteAgo" : "ui.lib.utils.5a399dd4a13f", {p0: m})
  const h = Math.floor(m / 60)
  if (h < 24) return t(h === 1 ? "ui.time.oneHourAgo" : "ui.lib.utils.e8c220f4858d", {p0: h})
  const day = Math.floor(h / 24)
  if (day < 7) return t(day === 1 ? "ui.time.oneDayAgo" : "ui.lib.utils.531d515667bd", {p0: day})
  return d.toLocaleDateString(getLocale(), { month: 'short', day: 'numeric' })
}

export function formatClock(value: unknown): string {
  const d = toDate(value)
  if (!d) return '--:--:--'
  return d.toLocaleTimeString(getLocale(), { hour12: false })
}

/** Scoped to one list projection: repeated days share labels, no global stale cache. */
export function createDateHeadingFormatter(reference = new Date()): (value: unknown) => string {
  const dayKey = (date: Date) => `${date.getFullYear()}/${date.getMonth()}/${date.getDate()}`
  const yesterday = new Date(reference.getTime())
  yesterday.setDate(yesterday.getDate() - 1)
  const year = reference.getFullYear()
  let selectedLocale = getLocale()
  const labels = new Map([[dayKey(reference), t("ui.lib.utils.d5f5a7a01073")], [dayKey(yesterday), t("ui.lib.utils.0c184871f658")]])
  let withinYear: Intl.DateTimeFormat | undefined
  let otherYear: Intl.DateTimeFormat | undefined
  return (value) => {
    if (selectedLocale !== getLocale()) {
      selectedLocale = getLocale()
      labels.clear()
      labels.set(dayKey(reference), t("ui.lib.utils.d5f5a7a01073"))
      labels.set(dayKey(yesterday), t("ui.lib.utils.0c184871f658"))
      withinYear = otherYear = undefined
    }
    const date = toDate(value)
    if (!date || !Number.isFinite(date.getTime())) return t("ui.lib.utils.c56a5bb657de")
    const key = dayKey(date)
    const cached = labels.get(key)
    if (cached !== undefined) return cached
    const formatter = date.getFullYear() === year
      ? withinYear ??= new Intl.DateTimeFormat(getLocale(), { month: 'long', day: 'numeric' })
      : otherYear ??= new Intl.DateTimeFormat(getLocale(), { year: 'numeric', month: 'long', day: 'numeric' })
    const label = formatter.format(date)
    labels.set(key, label)
    return label
  }
}

/* ============================ 文本 ============================ */

/** 把任意负载压成一行可预览的字符串。 */
export function stringify(value: unknown, limit = 400): string {
  if (value == null) return ''
  let text: string
  if (typeof value === 'string') text = value
  else {
    try {
      text = JSON.stringify(value, null, 2)
    } catch {
      text = String(value)
    }
  }
  if (text.length <= limit) return text
  return t("ui.lib.utils.d5d36950e5c2", {p0: (text.slice(0, limit)), p1: (text.length.toLocaleString(getLocale()))})
}

export function isProbablyJson(value: unknown): boolean {
  if (typeof value !== 'string') return false
  const trimmed = value.trim()
  if (!/^[[{]/.test(trimmed)) return false
  try {
    JSON.parse(trimmed)
    return true
  } catch {
    return false
  }
}

/** 稳定的短 id：同一条流里两个同文 block 也不会撞。 */
let seqCounter = 0
export function uid(prefix = 'b'): string {
  seqCounter += 1
  return `${prefix}_${Date.now().toString(36)}_${seqCounter.toString(36)}`
}

export function debounce<A extends unknown[]>(fn: (...args: A) => void, wait: number) {
  let timer: ReturnType<typeof setTimeout> | undefined
  const wrapped = (...args: A) => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => fn(...args), wait)
  }
  wrapped.cancel = () => {
    if (timer) clearTimeout(timer)
    timer = undefined
  }
  return wrapped
}

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* 回退到 execCommand */
  }
  let area: HTMLTextAreaElement | null = null
  let focused: Element | null = null
  try {
    focused = document.activeElement
    area = document.createElement('textarea')
    area.value = text
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    area?.remove()
    if (focused && focused instanceof HTMLElement && focused.isConnected) focused.focus({preventScroll: true})
  }
}

export function download(filename: string, content: string, mime = 'text/markdown') {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/* ============================ 键盘 ============================ */

export function isEditableTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return (
    tag === 'INPUT' ||
    tag === 'TEXTAREA' ||
    tag === 'SELECT' ||
    el.isContentEditable === true
  )
}

export function isMac(): boolean {
  if (typeof navigator === 'undefined') return false
  return /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent)
}

export function hasModifier(event: KeyboardEvent): boolean {
  return event.metaKey || event.ctrlKey
}
