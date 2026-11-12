/**
 * 按需语法高亮。
 *
 * highlight.js 的完整注册表约 1 MB，首屏绝不能打进去；这里只登记
 * harness 实际会遇到的 32 种语言，并且每种切独立 chunk，命中才下载。
 *
 * 从 `CodeBlock` 里抽出来是因为 diff 视图、文件查看器都要用同一套能力，
 * 放在组件里只能靠 import 副作用互相复用，等于隐式耦合。
 */

/** highlight.js 的 core 类型在 ESM 下没有 default 展开，这里显式声明用到的能力。 */
export interface HljsCore {
  getLanguage(name: string): unknown
  registerLanguage(name: string, fn: unknown): void
  highlight(code: string, options: { language: string; ignoreIllegals: boolean }): { value: string }
}

const loaded = new Map<string, Promise<HljsCore>>()

const ALIASES: Record<string, string> = {
  js: 'javascript',
  jsx: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  py: 'python',
  rb: 'ruby',
  rs: 'rust',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  console: 'bash',
  yml: 'yaml',
  md: 'markdown',
  html: 'xml',
  vue: 'xml',
  svg: 'xml',
  golang: 'go',
  kt: 'kotlin',
  ps: 'powershell',
  ps1: 'powershell',
  psm1: 'powershell',
  objc: 'objectivec',
}

export function normalizeLang(lang: string): string {
  const key = lang.trim().toLowerCase()
  return Object.hasOwn(ALIASES, key) ? ALIASES[key] : key
}

/**
 * highlight.js 的 lib/* 是 CJS。Vite 的互操作包装在不同版本 / 不同 chunk
 * 划分下时而把 `module.exports` 摊平成命名空间本身，时而只挂在 `.default` 上。
 * 直接取 `mod.default` 会在某些构建产物上拿到没有 getLanguage 的空壳，
 * 于是整段代码悄悄退化成无高亮的纯文本。这里统一两种形态。
 */
function interopDefault<T>(mod: unknown): T {
  const m = mod as { default?: T } & T
  return (m?.default ?? m) as T
}

async function loadCore(): Promise<HljsCore> {
  let pending = loaded.get('core')
  if (!pending) {
    pending = import('highlight.js/lib/core').then((m) => interopDefault<HljsCore>(m)).catch(error => {loaded.delete('core'); throw error})
    loaded.set('core', pending)
  }
  return pending
}

/**
 * 刻意写成 `() => import('highlight.js/lib/languages/xxx')` 的静态字面量形式：
 * 只有这样 Vite/Rollup 才能在构建期解析出真实路径并切出独立 chunk。
 * 用 `import(`highlight.js/lib/languages/${lang}`)` + @vite-ignore 会在浏览器里
 * 原样抛出裸模块说明符，永远解析失败，于是整段代码悄悄退化成无高亮的纯文本。
 */
const LANGUAGE_LOADERS: Record<string, () => Promise<unknown>> = {
  bash: () => import('highlight.js/lib/languages/bash'),
  c: () => import('highlight.js/lib/languages/c'),
  cpp: () => import('highlight.js/lib/languages/cpp'),
  csharp: () => import('highlight.js/lib/languages/csharp'),
  css: () => import('highlight.js/lib/languages/css'),
  diff: () => import('highlight.js/lib/languages/diff'),
  dockerfile: () => import('highlight.js/lib/languages/dockerfile'),
  elixir: () => import('highlight.js/lib/languages/elixir'),
  go: () => import('highlight.js/lib/languages/go'),
  graphql: () => import('highlight.js/lib/languages/graphql'),
  haskell: () => import('highlight.js/lib/languages/haskell'),
  ini: () => import('highlight.js/lib/languages/ini'),
  java: () => import('highlight.js/lib/languages/java'),
  javascript: () => import('highlight.js/lib/languages/javascript'),
  json: () => import('highlight.js/lib/languages/json'),
  kotlin: () => import('highlight.js/lib/languages/kotlin'),
  lua: () => import('highlight.js/lib/languages/lua'),
  markdown: () => import('highlight.js/lib/languages/markdown'),
  objectivec: () => import('highlight.js/lib/languages/objectivec'),
  perl: () => import('highlight.js/lib/languages/perl'),
  php: () => import('highlight.js/lib/languages/php'),
  powershell: () => import('highlight.js/lib/languages/powershell'),
  python: () => import('highlight.js/lib/languages/python'),
  r: () => import('highlight.js/lib/languages/r'),
  ruby: () => import('highlight.js/lib/languages/ruby'),
  rust: () => import('highlight.js/lib/languages/rust'),
  scala: () => import('highlight.js/lib/languages/scala'),
  sql: () => import('highlight.js/lib/languages/sql'),
  swift: () => import('highlight.js/lib/languages/swift'),
  typescript: () => import('highlight.js/lib/languages/typescript'),
  xml: () => import('highlight.js/lib/languages/xml'),
  yaml: () => import('highlight.js/lib/languages/yaml'),
}

/** 是否是这个模块认识、值得去加载的语言。 */
export function isHighlightable(lang: string): boolean {
  const key = normalizeLang(lang)
  return Boolean(key) && key !== 'plaintext' && key !== 'text' && Object.hasOwn(LANGUAGE_LOADERS, key)
}

export async function loadLanguage(lang: string): Promise<HljsCore | null> {
  const key = normalizeLang(lang)
  if (!Object.hasOwn(LANGUAGE_LOADERS, key)) return null
  const loader = LANGUAGE_LOADERS[key]
  const cached = loaded.get(key)
  if (cached) return cached.catch(() => null)
  const pending = Promise.all([loadCore(), loader()]).then(([core, module]) => {
    const defn = interopDefault<unknown>(module)
    if (typeof defn !== 'function') throw new Error(`Invalid highlight language: ${key}`)
    if (!core.getLanguage(key)) core.registerLanguage(key, defn)
    return core
  }).catch(error => {loaded.delete(key); throw error})
  loaded.set(key, pending)
  return pending.catch(() => null)
}

/**
 * 高亮一小段代码。语言不认识或加载失败时返回 `null`，
 * 由调用方决定退化成纯文本——**不要**把未转义的原文当 HTML 塞进去。
 */
export async function highlightToHtml(code: string, lang: string): Promise<string | null> {
  const key = normalizeLang(lang)
  if (!key || key === 'plaintext' || key === 'text') return null
  const core = await loadLanguage(key)
  if (!core) return null
  // 下载完成后多个代码块会同时恢复；分开任务，让浏览器有机会处理输入和绘制。
  await new Promise<void>(resolve => setTimeout(resolve, 0))
  try {
    return core.highlight(code, { language: key, ignoreIllegals: true }).value
  } catch {
    return null
  }
}
