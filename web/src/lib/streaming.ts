/**
 * 流式文本扫描器。
 *
 * LLM 边吐 token，我们边渲染，所以拿到的 Markdown 永远是"半个句子"：
 *   - 代码围栏只开不闭（```py\nimport os）
 *   - 行内公式只开不闭（质能方程 $E = mc^2）
 *   - 表格只写了一行半
 *
 * 这个模块提供两件事：
 *   1. `scanFences`  —— 找出所有围栏代码块（含未闭合的），让上层可以把
 *      "正在写的图" 提前从正文里摘出去单独渲染，而不是等它写完。
 *   2. `stabilizeMarkdown` —— 把未闭合的数学定界符补齐，让 KaTeX 不要
 *      因为半个 `$` 整段报错退回纯文本。
 *
 * 全部是纯函数、无依赖、可测。
 */

import { findInlineSvg } from './progressiveSvg'

/* ============================ 围栏 ============================ */

export type FenceChar = '`' | '~'

export interface FenceBlock {
  /** 围栏语言，已归一化（小写、去属性、截到首个空白）。 */
  lang: string
  /** 原始语言串（可能带 `{.python}` 之类的属性）。 */
  rawLang: string
  /** 是否为渲染块（```html type="renderer"），交给沙箱直接渲染而非代码展示。 */
  renderer: boolean
  /** 代码正文。 */
  code: string
  /** 围栏行之后的正文行数（含开栏行）。 */
  startLine: number
  /** 围栏是否已被闭合。 */
  closed: boolean
  /** 开栏行的原始文本，便于保留缩进语义。 */
  indent: string
  /** 正文起始/结束在整段文本中的字符偏移。 */
  bodyStart: number
  bodyEnd: number
  /** 整块（含开栏行）在整段文本中的字符区间。 */
  start: number
  end: number
}

interface FenceMarker {
  char: FenceChar
  length: number
}

const FENCE_OPEN = /^( {0,3}(?:>[ \t]? {0,3})*)(`{3,}|~{3,})[ \t]*([^\n]*)$/

function markerOf(line: string): FenceMarker | null {
  const match = FENCE_OPEN.exec(line)
  if (!match || (match[2]!.startsWith('`') && match[3]!.includes('`'))) return null
  return { char: match[2]!.charAt(0) as FenceChar, length: match[2]!.length }
}

/** 归一化语言标签：`Python` / `{.py}` / `mermaid {scale:1}` -> `python` / `` / `mermaid`。 */
export function normalizeLang(raw: string | null | undefined): string {
  if (!raw) return ''
  let value = raw.trim()
  if (!value) return ''
  // 去掉 `{.python}` / `{.python title="x"}` 属性块。
  if (value.startsWith('{')) {
    const close = value.indexOf('}')
    value = close >= 0 ? value.slice(close + 1) : ''
  }
  value = value.trim()
  if (!value) return ''
  // 取首个 token，兼容 `js title=x`、`json linenums`。
  value = value.split(/[\s,{:]/, 1)[0] ?? ''
  return value.toLowerCase()
}

/** 该语言是不是"能画图"的。 */
export function isChartLang(lang: string): boolean {
  const value = normalizeLang(lang)
  return (
    value === 'mermaid' ||
    value === 'svg' ||
    value === 'html' ||
    value === 'vega' ||
    value === 'vega-lite' ||
    value === 'chart' ||
    value === 'echarts' ||
    value === 'csv' ||
    value === 'tsv' ||
    value === 'chartjs' ||
    value === 'plotly'
  )
}

/** 渲染块判定：`html type="renderer"`（引号可有可无）。首 token 必须为 html。 */
export function isRendererInfo(rawLang: string | null | undefined): boolean {
  if (!rawLang) return false
  const info = /^html(?:\s+|$)/i.exec(rawLang.trim())
  if (!info) return false
  const meta = rawLang.trim().slice(info[0].length)
  const attribute = /\s*([^\s='"]+)(?:[ \t]*=[ \t]*(?:"([^"]*)"|'([^']*)'|([^\s'"=]+)))?(?=\s|$)/y
  let renderer = false
  let seenType = false
  let offset = 0
  while (offset < meta.length) {
    attribute.lastIndex = offset
    const match = attribute.exec(meta)
    if (!match) return !meta.slice(offset).trim() && renderer
    offset = attribute.lastIndex
    if (match[1].toLowerCase() !== 'type') continue
    if (seenType) return false
    seenType = true
    renderer = (match[2] ?? match[3] ?? match[4] ?? '').toLowerCase() === 'renderer'
  }
  return renderer
}

/**
 * 扫描全部围栏代码块。
 *
 * 逐行走，保持一个开栏记录；遇到同字符且长度 >= 开栏的独立围栏行即闭合。
 * 未闭合的块 body 取到文本末尾，`closed:false`。
 */
export function scanFences(text: string): FenceBlock[] {
  if (!text) return []
  const blocks: FenceBlock[] = []
  const lines = text.split('\n')

  let offset = 0
  let open: { marker: FenceMarker; indent: string; rawLang: string; line: number; bodyStart: number; start: number } | null =
    null

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]!
    const lineStart = offset
    const lineEnd = offset + line.length

    if (!open) {
      const marker = markerOf(line)
      if (marker) {
        const info = FENCE_OPEN.exec(line)!
        open = {
          marker,
          indent: info[1] ?? '',
          rawLang: (info[3] ?? '').trim(),
          line: index,
          // 正文从开栏行之后开始。
          bodyStart: Math.min(lineEnd + 1, text.length),
          start: lineStart,
        }
      }
    } else {
      const marker = markerOf(line)
      const closingInfo = FENCE_OPEN.exec(line)
      // 围栏正文里的引用标记是代码，不能拿来闭合外层围栏。
      const sameContainer = (closingInfo?.[1]?.match(/>/g)?.length ?? 0) === (open.indent.match(/>/g)?.length ?? 0)
      const closes =
        marker && sameContainer &&
        marker.char === open.marker.char &&
        marker.length >= open.marker.length &&
        !(closingInfo?.[3] ?? '').trim()
      if (closes) {
        const bodyEnd = lineStart
        blocks.push({
          lang: normalizeLang(open.rawLang),
          rawLang: open.rawLang,
          renderer: isRendererInfo(open.rawLang),
          code: text.slice(open.bodyStart, bodyEnd).replace(/\n$/, ''),
          startLine: open.line,
          closed: true,
          indent: open.indent,
          bodyStart: open.bodyStart,
          bodyEnd,
          start: open.start,
          end: Math.min(lineEnd, text.length),
        })
        open = null
      }
    }

    offset = lineEnd + 1
  }

  if (open) {
    const bodyEnd = text.length
    blocks.push({
      lang: normalizeLang(open.rawLang),
      rawLang: open.rawLang,
      renderer: isRendererInfo(open.rawLang),
      // An open SVG attribute may end at this newline. It is a received
      // coordinate delimiter, not presentation whitespace to remove.
      code: text.slice(open.bodyStart, bodyEnd),
      startLine: open.line,
      closed: false,
      indent: open.indent,
      bodyStart: open.bodyStart,
      bodyEnd,
      start: open.start,
      end: bodyEnd,
    })
  }

  return blocks
}

/* ============================ 数学定界符 ============================ */

/**
 * 补齐未闭合的 `$` / `$$`，让 KaTeX 在流式期也能出公式。
 *
 * 只在"确定存在未闭合定界符"时才动手，而且跳过围栏与行内代码，
 * 避免把 shell 里的 `$VAR` 当成公式开口。
 */
export function stabilizeMath(text: string): string {
  if (!text || text.indexOf('$') < 0) return text

  const fences = scanFences(text)
  let fenceIndex = 0
  let open: { size: number; flow: boolean; contentStart: number } | null = null
  let i = 0
  let lineStart = 0
  let scannedTo = 0
  while (i < text.length) {
    while (scannedTo < i) {
      if (text[scannedTo] === '\n') lineStart = scannedTo + 1
      scannedTo += 1
    }
    const fence = fences[fenceIndex]
    if (fence && i >= fence.end) {
      fenceIndex += 1
      continue
    }
    // 数学块中的反引号属于公式原文，不开启 Markdown 代码块。
    if (!open?.flow && fence && i >= fence.start) {
      i = fence.end
      fenceIndex += 1
      open = null
      continue
    }
    if (!open && text[i] === '\\') {
      i += 2
      continue
    }
    if (!open && text[i] === '`') {
      let end = i + 1
      while (text[end] === '`') end += 1
      const size = end - i
      const runs = /`+/g
      runs.lastIndex = end
      let closing: RegExpExecArray | null
      while ((closing = runs.exec(text)) !== null) {
        if (closing[0].length === size) break
      }
      // 未闭合的反引号是普通文本，不吞掉后面的公式。
      i = closing ? closing.index + size : end
      continue
    }
    if (text[i] !== '$') {
      // 行内公式不能跨过段落边界，避免把补齐符追加到后续段落。
      if (open && !open.flow && text[i] === '\n' && /^[ \t]*\r?\n/.test(text.slice(i + 1))) open = null
      i += 1
      continue
    }
    let end = i + 1
    while (text[end] === '$') end += 1
    const size = end - i
    const atStart = i - lineStart <= 3 && /^[ ]{0,3}$/.test(text.slice(lineStart, i))
    const lineEnd = atStart && size >= 2 ? text.indexOf('\n', end) : -1
    const suffix = atStart && size >= 2 ? text.slice(end, lineEnd < 0 ? text.length : lineEnd) : ''
    if (open?.flow) {
      if (atStart && size >= open.size && /^[ \t\r]*$/.test(suffix)) open = null
    } else if (open) {
      if (size === open.size) open = null
    } else {
      // 与 remark-math 一致：行首至少两个美元且元信息不含美元才是块公式。
      open = { size, flow: size >= 2 && atStart && !suffix.includes('$'), contentStart: end }
    }
    i = end
  }
  if (!open || !text.slice(open.contentStart).trim()) return text
  const closing = '$'.repeat(open.size)
  return text + (open.flow ? (text.endsWith('\n') ? '' : '\n') : '') + closing
}

/**
 * 流式期统一入口：补齐数学定界符。
 * 围栏本身由渲染层单独摘出去渲染，所以这里原样保留。
 */
export function stabilizeMarkdown(text: string): string {
  return stabilizeMath(text)
}


/* ============================ 分段 ============================ */

/**
 * 一条流式消息的渲染分段。
 *
 * 为什么要在送进 Markdown 之前先切一刀：
 *
 * 1. **围栏闭合状态要精确。** 以前 `closed` 直接取「整条消息是否还在流式输出」，
 *    于是第一张已经写完的图，在模型继续写第二段文字时仍被当成「还在写」：
 *    mermaid 一直挂在防抖队列上重渲染，收尾的生长动画也永远不播。
 * 2. **正文内联画图。** 模型被要求「画个图」时经常不进围栏，直接在段落里吐
 *    `<svg>…</svg>`。以前这种内容会被 Markdown 当普通文本，标签直接糊在屏幕上。
 * 3. **性能。** 整段 Markdown 每来一个 token 都要重跑一遍 remark/rehype，
 *    包括早已定稿的表格、代码块和公式。切段之后，已完成的段各自 memo，
 *    流式期只重解析最后一段。
 */
export type StreamSegment =
  | { kind: 'markdown'; text: string; key: string; last: boolean }
  | {
      kind: 'chart'
      code: string
      lang: string
      closed: boolean
      key: string
      last: boolean
    }
  | {
      kind: 'renderer'
      code: string
      closed: boolean
      key: string
      last: boolean
    }

/** 正文里的 `<svg>` 只有独占一行时才提升为图，避免误伤句子里的行内标签。 */
const INLINE_SVG_AT_LINE_START = /(^|\n)[ \t]{0,3}(?=<svg\b)/i

function isChartBody(lang: string, code: string): boolean {
  if (isChartLang(lang)) return true
  // 没标语言的围栏里塞了 SVG（模型很爱这么干），照样画。
  return !lang && /<svg\b/i.test(code)
}

/**
 * 把流式文本切成「Markdown 段」与「图表段」。
 *
 * 图表段来自两处：围栏代码块（mermaid / svg / vega…），
 * 以及正文里独占成行的裸 `<svg>`。两者都不会闭合状态失真。
 */
export function segmentStream(text: string): StreamSegment[] {
  if (!text.trim()) return []
  const out: StreamSegment[] = []
  let cursor = 0

  // 正文先攒着，遇到图表再 flush —— 这样围栏前后的段落各自成段，
  // React 才能按段 memo，流式期只重解析最后一段。
  let pending = ''
  const flush = () => {
    if (!pending.trim()) {
      pending = ''
      return
    }
    out.push({ kind: 'markdown', text: pending, key: `m${out.length}`, last: false })
    pending = ''
  }
  const addChart = (code: string, lang: string, closed: boolean) => {
    flush()
    out.push({ kind: 'chart', code, lang, closed, key: `c${out.length}`, last: false })
  }
  const addRenderer = (code: string, closed: boolean) => {
    flush()
    out.push({ kind: 'renderer', code, closed, key: `r${out.length}`, last: false })
  }

  /** gap = 围栏之间的正文；里面可能藏着独占一行的裸 `<svg>`。 */
  const emitGap = (gap: string) => {
    if (!gap) return
    const re = new RegExp(INLINE_SVG_AT_LINE_START.source, 'gi')
    let at = 0
    let match: RegExpExecArray | null
    while ((match = re.exec(gap)) !== null) {
      const bodyStart = match.index + match[0].length
      const hit = findInlineSvg(gap.slice(bodyStart))
      if (!hit) break
      // 还没闭合时，后面必须真的有 SVG 内容才算「正在画」。
      // 否则正文里随口一句 `use <svg> element` 就会把半篇回答吞成图。
      if (!hit.closed && !gap.slice(bodyStart + hit.start).includes('<')) break
      const from = bodyStart + hit.start
      const to = bodyStart + hit.end
      pending += gap.slice(at, from)
      addChart(gap.slice(from, to), 'svg', hit.closed)
      at = to
      re.lastIndex = to
    }
    pending += gap.slice(at)
  }

  for (const fence of scanFences(text)) {
    emitGap(text.slice(cursor, fence.start))
    // 只把「顶格」的围栏提出来。缩进过的围栏（列表项、引用里的图）切段会
    // 把一个列表劈成两个，那种结构交给 Markdown 自己的 code 组件兜底。
    if (fence.indent === '' && fence.renderer) {
      // 渲染块优先于图表判定：它也是 html，但语义是「整页交给沙箱」。
      addRenderer(fence.code, fence.closed)
    } else if (fence.indent === '' && isChartBody(fence.lang, fence.code)) {
      addChart(fence.code, fence.lang || 'svg', fence.closed)
    } else {
      // 非图表围栏原样保留在正文中。
      pending += text.slice(fence.start, fence.end)
    }
    cursor = fence.end
  }
  emitGap(text.slice(cursor))
  flush()

  const tail = out[out.length - 1]
  if (!tail) return []
  out[out.length - 1] = { ...tail, last: true }
  return out
}
