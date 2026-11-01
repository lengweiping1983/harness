import { t } from '@/i18n'
/**
 * 原始媒体标签归一化。
 *
 * 背景：渲染管线为了安全没开 `rehype-raw`，Markdown 里的原始 HTML 会被整体丢弃
 * （`skipHtml`）。这条取舍本身是对的，但模型极爱直接吐
 *
 *     <video src="demo.mp4" controls></video>
 *     <img src="chart.png">
 *
 * 于是一段本来完全能播的视频，在界面上凭空消失 —— 用户看到的是"模型答了，
 * 但视频没了"，比报错更难排查。
 *
 * 做法：把这三类标签**改写成等价的 Markdown 链接**，继续走已经验证过的
 * `MarkdownLink` / `MediaPlayer` / 图片灯箱。不放开 `rehype-raw`，
 * 攻击面一点没变，只是把"丢弃"换成"翻译"。
 *
 * 两条硬约束：
 *   1. 围栏代码块里的内容**原样保留** —— 教人写 HTML 的代码示例不能被改写。
 *   2. 取不到 `src` 的标签原样返回，交给上层按原逻辑处理，不猜测。
 */

import { scanFences } from './streaming'

/** 读取一个 HTML 属性值，兼容双引号 / 单引号 / 无引号三种写法。 */
function attrOf(html: string, name: string): string {
  const re = new RegExp(`\\b${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s"'\u003e]+))`, 'i')
  const m = re.exec(html)
  if (!m) return ''
  return (m[1] ?? m[2] ?? m[3] ?? '').trim()
}

/** 取路径末段做链接文案；取不到就用调用方给的兜底。 */
function basename(src: string, fallback: string): string {
  const clean = src.split(/[?#]/, 1)[0] ?? ''
  const parts = clean.split(/[\\/]/)
  const last = parts[parts.length - 1] ?? ''
  return last.trim() || fallback
}

/** `src` 里带空格或括号时必须用尖括号包起来，否则链接会在中途断开。 */
function linkTarget(src: string): string {
  return /[\s()<>]/.test(src) ? `<${src}>` : src
}

/** 图片的 alt / title 里不能出现会提前闭合语法的字符。 */
function safeText(value: string): string {
  return value.replace(/[\\\[\]\n]/g, ' ').replace(/\s+/g, ' ').trim()
}

/** 从 `<video …>` 一直吃到配对的闭合标签；没闭合就只吃开标签。 */
function consumeElement(text: string, from: number, tag: string): { inner: string; end: number } {
  const closeRe = new RegExp(`</${tag}\\s*>`, 'gi')
  closeRe.lastIndex = from
  const m = closeRe.exec(text)
  if (!m) return { inner: '', end: from }
  return { inner: text.slice(from, m.index), end: m.index + m[0].length }
}

/** `<video>` 的地址可能写在子标签 `<source src>` 上。 */
function sourceOf(inner: string): string {
  const re = /<source\b([^>\u003e]*)>/gi
  let m: RegExpExecArray | null
  while ((m = re.exec(inner)) !== null) {
    const src = attrOf(m[1] ?? '', 'src')
    if (src) return src
  }
  return ''
}

function rewrite(haystack: string): string {
  // 正则带 g 标志且复用 lastIndex，每次进来都新建一份，免得跨调用串味。
  const open = /<(video|audio|img)\b([^>\u003e]*)>/gi
  let out = ''
  let cursor = 0
  let m: RegExpExecArray | null

  while ((m = open.exec(haystack)) !== null) {
    const tag = (m[1] ?? '').toLowerCase()
    const attrs = m[2] ?? ''
    const openEnd = m.index + m[0].length

    if (tag === 'img') {
      const src = attrOf(attrs, 'src')
      // 没有 src 就保持原样，交给上层按原逻辑处理，不猜。
      if (!src) continue
      out += haystack.slice(cursor, m.index)
      const alt = safeText(attrOf(attrs, 'alt'))
      const title = attrOf(attrs, 'title')
      const titlePart = title && !title.includes('"') && !title.includes('\n') ? ` "${title}"` : ''
      out += `![${alt}](${linkTarget(src)}${titlePart})`
      cursor = openEnd
      continue
    }

    const { inner, end } = consumeElement(haystack, openEnd, tag)
    const src = attrOf(attrs, 'src') || sourceOf(inner)
    if (!src) continue
    out += haystack.slice(cursor, m.index)
    const kind = tag === 'video' ? t("ui.lib.mediaTags.c20f7618d330") : t("ui.lib.mediaTags.296c632ec857")
    out += `[${basename(src, kind)}](${linkTarget(src)})`
    cursor = end
    open.lastIndex = end
  }

  return out + haystack.slice(cursor)
}

/**
 * 把正文里的原始 `<img>` / `<video>` / `<audio>` 改写成 Markdown 链接。
 *
 * 围栏代码块（闭合与否都算）先按区间切出来原样保留，只对夹在中间的正文段
 * 单独改写。这里刻意**不做**"挖空成等长空格再拼回"那套：改写会改变长度，
 * 偏移量随之失效，围栏后的内容会被切错位置。
 */
export function normalizeMediaTags(text: string): string {
  if (!text || !/<(?:video|audio|img)\b/i.test(text)) return text

  const fences = scanFences(text)
  if (fences.length === 0) return rewrite(text)

  const pieces: string[] = []
  let at = 0
  for (const fence of fences) {
    pieces.push(rewrite(text.slice(at, fence.start)))
    pieces.push(text.slice(fence.start, fence.end))
    at = fence.end
  }
  pieces.push(rewrite(text.slice(at)))
  return pieces.join('')
}
