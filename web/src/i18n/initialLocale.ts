/** 主页面使用缓存或中文默认值；共享页面保留浏览器语言兜底。 */
export function initialLocale(): 'zh-CN' | 'en' {
  let locale: 'zh-CN' | 'en' = typeof window !== 'undefined' && window.location.pathname === '/shared' && !navigator.language.toLowerCase().startsWith('zh') ? 'en' : 'zh-CN'
  try {const saved = localStorage.getItem('harness.language.effective.v1'); if (saved === 'zh-CN' || saved === 'en') locale = saved} catch { /* 存储不可用时保持入口默认语言。 */ }
  return locale
}
