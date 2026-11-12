import { useSyncExternalStore } from 'react'
import {initialLocale} from './initialLocale'

export type Locale = 'zh-CN' | 'en'
export type LanguagePreference = 'auto' | Locale
const tables: Partial<Record<Locale, Record<string, string>>> = {}
const pending = new Map<Locale, Promise<void>>()
const loaders = {
  'zh-CN': () => import('./locales/zh-CN.json'),
  en: () => import('./locales/en.json'),
}
export function loadLocale(next: Locale): Promise<void> {
  if (tables[next]) return Promise.resolve()
  let request = pending.get(next)
  if (!request) {
    request = loaders[next]().then(module => {tables[next] = module.default}).finally(() => pending.delete(next))
    pending.set(next, request)
  }
  return request
}
const subscribers = new Set<() => void>()
let locale: Locale = initialLocale()
// 所有运行环境都只读取当前语言，另一语言由实际切换请求加载。
await loadLocale(locale)
let selection = 0
if (typeof document !== 'undefined') document.documentElement.lang = locale
export function getLocale(): Locale { return locale }
export async function prepareLocale(next: Locale): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    await Promise.race([loadLocale(next), new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`Locale load timed out: ${next}`)), 10000)
    })])
  } finally {clearTimeout(timer)}
}
export function setLocale(next: Locale): Promise<void> {
  if (next !== 'zh-CN' && next !== 'en') return Promise.resolve()
  const ticket = ++selection
  const commit = () => {
    if (ticket !== selection) return
    if (typeof document !== 'undefined') document.documentElement.lang = next
    try { localStorage.setItem('harness.language.effective.v1', next) } catch { /* Storage is optional. */ }
    if (locale === next) return
    locale = next
    subscribers.forEach(listener => listener())
  }
  if (tables[next]) {commit(); return Promise.resolve()}
  return prepareLocale(next).then(commit)
}
export function useLocale(): Locale {
  return useSyncExternalStore(listener => { subscribers.add(listener); return () => { subscribers.delete(listener) } }, getLocale, getLocale)
}
/** 动态产品错误码只在已有资源中查找，未知码由调用方明确处理。 */
export function hasTranslation(key: string): boolean { return Object.hasOwn(tables[locale]!, key) }
export function t(key: string, values?: Record<string, unknown>): string {
  if (!Object.hasOwn(tables[locale]!, key)) throw new Error(`Missing ${locale} resource: ${key}`)
  const text = tables[locale]![key]
  if (!values) return text
  return text.replace(/\{(\w+)\}/g, (original, name: string) => Object.hasOwn(values, name) ? String(values[name]) : original)
}
