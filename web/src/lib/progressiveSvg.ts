/**
 * 渐进式 SVG 解析 + 增量绘制。
 *
 * 模型吐 SVG 是流式的，所以拿到的往往是：
 *   `<svg viewBox="0 0 400 300"><g transform="translate(20,20)"><rect w="3`
 * 直接 `dangerouslySetInnerHTML` 会得到一张空白图 + 控制台报错；
 * 等待闭合又完全丧失「边画边展示」。
 *
 * 这一层做四件事：
 *   1. 截断安全：丢掉末尾残缺的 `<...` 片段和未闭合注释。
 *   2. 清洗：移除 script / foreignObject / on* 事件属性 / javascript: /
 *      外部 href，避免模型输出的 SVG 变成攻击面。
 *   3. 补齐：按标签栈补上缺失的闭合标签，得到结构完整的 SVG；
 *      标签之间的**文本节点**也会保留（坐标轴标签、图例、标题都靠它）。
 *   4. 增量：额外产出一棵节点树，`patchSvg` 按位就地协调 DOM，
 *      只改动真正变化的那一个节点。
 *
 * 第 4 点是「边画边展示」体验的关键。如果每个 token 都整块 innerHTML 重解析：
 *   - 浏览器要把整棵 SVG 从零重建一遍，长图会明显卡；
 *   - 已画好的部分会整帧重绘，出现闪烁；
 *   - 已有节点身份和用户查看位置不能保持。
 * 就地补丁把这三件事一起消掉：模型画到哪，就只有那一段在动。
 *
 * 在此之上还有一层 `strokePrep` 绘制动画：新闭合的图元按
 * 「描边生长 → 填色淡入 → 文字淡入」的顺序播一次，让图的呈现本身
 * 也是「画出来」的，而不是一整块突然摆上画布。
 *
 * 纯字符串处理，无 DOM 依赖，浏览器和测试环境行为一致；
 * 只有 `patchSvg` / `strokePrep` 需要真实 DOM。
 */

import { pathDataPrefix } from './svgPath'

const BLOCKED_TAGS = new Set([
  'script',
  'foreignobject',
  'iframe',
  'object',
  'embed',
  'audio',
  'video',
  'animate',
  'animatecolor',
  'animatemotion',
  'animatetransform',
  'set',
  'handler',
  'listener',
  'style',
])

const IMAGE_TAGS = new Set(['style', 'animate', 'animatecolor', 'animatemotion', 'animatetransform', 'set'])
const URL_ATTRS = new Set(['href', 'xlink:href', 'src', 'from', 'to', 'values'])

const SAFE_URL =
  /^(?:#[\w:.-]*|data:image\/(?:png|jpe?g|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=\s]*|[a-z0-9._~-][a-z0-9._~\/-]*\.(?:png|jpe?g|gif|webp|avif|svg))$/i

export type SvgAttrs = Record<string, string>

/** 元素节点。 */
export interface SvgNode {
  kind: 'element'
  tag: string
  attrs: SvgAttrs
  children: SvgChild[]
}

/** 文本节点（`<text>`、`<title>`、`<tspan>` 的内容）。 */
export interface SvgText {
  kind: 'text'
  text: string
}

export type SvgChild = SvgNode | SvgText

export interface ParsedSvg {
  /** 补齐+清洗后的完整 `<svg>…</svg>` 字符串，可直接渲染。 */
  markup: string
  /** 供 `patchSvg` 使用的节点树（与 `markup` 同源）。 */
  root: SvgNode | null
  /** viewBox 或推断出的尺寸。 */
  viewBox: string | null
  width: number | null
  height: number | null
  /** 是否因为流式截断而补齐过标签。 */
  repaired: boolean
  /** 内容里有可动画的路径类元素。 */
  drawable: number
  /** 输入是否已经是一个完整可渲染的 svg。 */
  complete: boolean
}

const NAME_RE = /^[\s]*([A-Za-z_][\w:.-]*)/
const ATTR_RE =
  /([A-Za-z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>/]+))/g

const DRAWABLE = new Set([
  'path',
  'line',
  'polyline',
  'polygon',
  'circle',
  'ellipse',
  'rect',
])

/* ============================ 属性清洗 ============================ */

function isEventAttr(name: string): boolean {
  return /^on/i.test(name)
}

// SVG is inserted into the document, so model CSS must not style the app or
// fetch external resources. Keep local presentation declarations only.
const PRESENTATION_CSS = new Set([
  'fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width', 'stroke-opacity',
  'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'stroke-dashoffset',
  'opacity', 'color', 'font-family', 'font-size', 'font-weight', 'font-style',
  'text-anchor', 'dominant-baseline', 'alignment-baseline', 'letter-spacing',
  'word-spacing', 'paint-order', 'vector-effect', 'visibility', 'display',
])

function safePresentation(value: string): boolean {
  if (/[\\<>@\u0000-\u001f]/.test(value) || /(?:javascript|data|https?):|expression\s*\(/i.test(value)) return false
  // Only intra-SVG paint/filter references are accepted.
  const withoutLocalUrls = value.replace(/url\(\s*(['"]?)#[\w:.-]+\1\s*\)/gi, '')
  return !/url\s*\(/i.test(withoutLocalUrls)
}

function presentationStyle(value: string): string {
  return value.split(';').map((declaration) => {
    const colon = declaration.indexOf(':')
    if (colon < 0) return ''
    const name = declaration.slice(0, colon).trim().toLowerCase(), body = declaration.slice(colon + 1).trim()
    return PRESENTATION_CSS.has(name) && safePresentation(body) ? `${name}:${body}` : ''
  }).filter(Boolean).join(';')
}

function keepAttr(name: string, value: string, tagName: string, image = false): boolean {
  if (isEventAttr(name)) return false
  const lower = name.toLowerCase()
  if (image && ['from', 'to', 'values'].includes(lower) && !safePresentation(value.replace(/[\r\n\t]/g, ' '))) return false
  if (['fill', 'stroke', 'filter', 'clip-path', 'mask', 'cursor', 'color', 'marker-start', 'marker-mid', 'marker-end'].includes(lower) && !safePresentation(value)) return false
  if (URL_ATTRS.has(lower) && !(image && ['from', 'to', 'values'].includes(lower)) && !SAFE_URL.test(value.trim())) return false
  // `<use>`/`image` 指向外部文件会发起请求，也一并拦掉非 data/相对路径。
  if (tagName === 'use' && lower === 'href') {
    return value.trim().startsWith('#')
  }
  return true
}

/** 把原始属性串解析成 `{ name: value }`，顺带完成白名单清洗。 */
function parseAttrs(raw: string, tagName: string, image = false): SvgAttrs {
  const out: SvgAttrs = {}
  ATTR_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = ATTR_RE.exec(raw)) !== null) {
    // 两个属性之间夹杂的裸文本（畸形输出）直接丢掉。
    const name = match[1]!
    const value = match[2] ?? match[3] ?? match[4] ?? ''
    if (!keepAttr(name, value, tagName, image)) continue
    if (!image && name.toLowerCase() === 'style') {
      const safe = presentationStyle(value)
      if (safe) out[name] = safe
    } else out[name] = value
  }
  return out
}

function serializeAttrs(attrs: SvgAttrs): string {
  let out = ''
  for (const [name, value] of Object.entries(attrs)) {
    out += ` ${name}="${value.replace(/"/g, '&quot;')}"`
  }
  return out
}

function hasDangerousUrl(raw: string): boolean {
  return /=\s*(?:"\s*javascript:[^"]*"|'\s*javascript:[^']*'|javascript:[^\s>]+)/i.test(raw)
}

/** 从标签文本里切出「标签名之后、结尾 `>` 之前」的原始属性串。 */
function rawAttrsOf(tagText: string, name: string, selfClosing: boolean): string {
  const attrsStart = tagText.indexOf(name) + name.length
  const attrsEnd = selfClosing ? tagText.lastIndexOf('/') : tagText.lastIndexOf('>')
  return tagText.slice(attrsStart, attrsEnd < attrsStart ? undefined : attrsEnd)
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/** 图片按 XML 解码文本，避免重新序列化后把 &amp; 等实体显示成源码。 */
function decodeXmlText(value: string): string {
  const named: Record<string, string> = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'"}
  return value.replace(/&(amp|lt|gt|quot|apos|#\d+|#x[\da-fA-F]+);/g, (raw, entity: string) => {
    if (!entity.startsWith('#')) return named[entity]!
    const code = entity[1] === 'x' ? Number.parseInt(entity.slice(2), 16) : Number(entity.slice(1))
    return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) ? String.fromCodePoint(code) : raw
  })
}

/* ============================ 提取 / 修补 ============================ */

/**
 * 从任意文本里取出 `<svg>` 主体。
 * 支持：整段就是 svg、svg 前后有说明文字、`<svg>` 属性带换行。
 */
function extractSvg(source: string): { start: number; end: number } | null {
  const open = /<svg\b/i.exec(source)
  if (!open) return null
  const from = open.index
  const close = /<\/svg\s*>/i.exec(source.slice(from))
  if (!close) return null
  return { start: from, end: from + close.index + close[0].length }
}

/**
 * 流式期的内联绘图定位。
 *
 * 模型被要求「画个图」时，经常不进围栏，直接在正文里吐 `<svg>…</svg>`。
 * 这里既要能认出已经写完的，也要能认出**只吐了开头**的
 * （`<svg viewBox="0 0 200 200"><circle cx="10`），好让上层提前把
 * 画布摆出来 —— 这正是「边画边展示」在正文场景下的入口。
 */
export function findInlineSvg(source: string): { start: number; end: number; closed: boolean } | null {
  const open = /<svg\b/i.exec(source)
  if (!open) return null
  const from = open.index
  const close = /<\/svg\s*>/i.exec(source.slice(from))
  if (!close) return { start: from, end: source.length, closed: false }
  return { start: from, end: from + close.index + close[0].length, closed: true }
}

/** A long path can grow before its `d` quote or tag closes. Other incomplete
 * attributes/tags still wait; all completed attributes use the same sanitizer. */
function partialPath(tag: string): string {
  const opening = /^<path(?=\s)/i.exec(tag)
  if (!opening) return ''
  let offset = opening[0].length, completeAttrs = '', path: string | null = null
  while (offset < tag.length) {
    const attribute = /^\s+([A-Za-z_:][\w:.-]*)\s*=\s*/.exec(tag.slice(offset))
    if (!attribute) break
    const start = offset
    offset += attribute[0].length
    const name = attribute[1]!, quote = tag[offset]
    if (quote !== '"' && quote !== "'") break
    const valueStart = ++offset, close = tag.indexOf(quote, offset)
    if (close < 0) {
      if (name === 'd') path = pathDataPrefix(tag.slice(valueStart))
      break
    }
    offset = close + 1
    completeAttrs += tag.slice(start, offset)
    if (name === 'd') path = pathDataPrefix(tag.slice(valueStart, close), true)
  }
  if (!path) return ''
  const attrs = parseAttrs(completeAttrs, 'path')
  attrs.d = path
  return `<path${serializeAttrs(attrs)}/>`
}

/** Truncate incomplete tails, retaining received path segments when possible. */
function cutPartialTail(source: string): string {
  let end = source.length
  const comment = source.lastIndexOf('<!--')
  const commentEnd = source.lastIndexOf('-->')
  if (comment >= 0 && comment > commentEnd) end = comment
  const open = source.lastIndexOf('<')
  if (open >= 0 && open < end) {
    // A `>` inside an attribute is not a tag boundary.
    let quote: string | null = null, closed = false
    for (let i = open + 1; i < end; i += 1) {
      const ch = source[i]!
      if (quote) { if (ch === quote) quote = null }
      else if (ch === '"' || ch === "'") quote = ch
      else if (ch === '>') { closed = true; break }
    }
    if (!closed) return source.slice(0, open) + partialPath(source.slice(open, end))
  }
  return source.slice(0, end)
}

function parseLength(value: string | null): number | null {
  if (!value) return null
  const match = /^\s*(-?[\d.]+)\s*(px|pt|pc|in|em|rem|%)?\s*$/.exec(value)
  if (!match) return null
  const num = Number(match[1])
  if (!Number.isFinite(num) || num <= 0) return null
  const unit = match[2] ?? ''
  if (unit === '%') return null
  if (unit === 'em' || unit === 'rem') return num * 16
  if (unit === 'pt') return (num * 96) / 72
  if (unit === 'in') return num * 96
  if (unit === 'pc') return num * 16
  return num
}

function firstAttr(tagText: string, name: string): string | null {
  const re = new RegExp(`\\b${name.replace(/[:]/g, '\\:')}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, 'i')
  const match = re.exec(tagText)
  if (!match) return null
  return match[1] ?? match[2] ?? null
}

/** 删掉 script / foreignObject 这类整段内容（而不是只删标签壳）。 */
function dropBlockedElements(source: string, image = false): string {
  let out = source
  for (const tag of BLOCKED_TAGS) {
    if (image && IMAGE_TAGS.has(tag)) continue
    const paired = new RegExp(`<${tag}\\b[\\s\\S]*?<\\/${tag}\\s*>`, 'gi')
    out = out.replace(paired, '')
    out = out.replace(new RegExp(`<\\/${tag}\\s*>`, 'gi'), '')
    out = out.replace(new RegExp(`<${tag}\\b[^>]*>`, 'gi'), '')
  }
  return out
}

function countDrawable(children: SvgChild[]): number {
  let total = 0
  for (const child of children) {
    if (child.kind === 'text') continue
    if (DRAWABLE.has(child.tag.toLowerCase())) total += 1
    total += countDrawable(child.children)
  }
  return total
}

function serialize(children: SvgChild[]): string {
  let out = ''
  for (const child of children) {
    if (child.kind === 'text') {
      out += escapeXml(child.text)
      continue
    }
    out += `<${child.tag}${serializeAttrs(child.attrs)}>`
    out += serialize(child.children)
    out += `</${child.tag}>`
  }
  return out
}

/**
 * 核心：把（可能残缺的）SVG 源码修成可以直接渲染的 markup + 节点树。
 */
export function parseProgressiveSvg(source: string): ParsedSvg | null {
  const span = extractSvg(source)
  if (!span) {
    // 也许只吐了 `<svg viewBox=...` 连闭合都没有 —— 仍然尝试。
    const loose = /<svg\b/i.exec(source)
    if (!loose) return null
    return repair(source.slice(loose.index))
  }
  return repair(source.slice(span.start, span.end))
}

/**
 * 完整的样式／动画 SVG 仅供 img 使用，不返回可插入宿主的节点树。
 * 图片上下文隔离 CSS，禁用脚本及外部资源；不能把此 markup 内联进 DOM。
 */
export function parseSvgImage(source: string): Omit<ParsedSvg, 'root'> | null {
  if (!/<(?:style|animate(?:color|motion|transform)?|set)\b|\banimation(?:-[\w-]+)?\s*:/i.test(source)) return null
  const span = extractSvg(source)
  if (!span) return null
  const parsed = repair(source.slice(span.start, span.end), true)
  if (!parsed?.root) return null
  parsed.root.attrs.xmlns = SVG_NS
  parsed.root.attrs['xmlns:xlink'] = 'http://www.w3.org/1999/xlink'
  const cleanAnimations = (node: SvgNode) => {
    node.children = node.children.filter(child => {
      if (child.kind === 'text') return true
      const tag = child.tag.toLowerCase()
      if (IMAGE_TAGS.has(tag) && tag !== 'style' && tag !== 'animatemotion') {
        const attribute = Object.entries(child.attrs).find(([key]) => key.toLowerCase() === 'attributename')?.[1]?.toLowerCase()
        // 下载后独立打开也不能通过 SMIL 修改链接、事件或任意内联样式。
        if (!attribute || !ANIMATED_ATTRIBUTES.has(attribute)) return false
      }
      cleanAnimations(child)
      return true
    })
  }
  cleanAnimations(parsed.root)
  const {root, ...metadata} = parsed
  return {...metadata, markup: serialize([root])}
}

const ANIMATED_ATTRIBUTES = new Set([
  ...PRESENTATION_CSS, 'transform', 'viewbox', 'x', 'y', 'x1', 'y1', 'x2', 'y2',
  'cx', 'cy', 'r', 'rx', 'ry', 'width', 'height', 'd', 'points', 'pathlength',
  'offset', 'stop-color', 'stop-opacity', 'dx', 'dy', 'rotate',
])

function repair(svgChunk: string, image = false): ParsedSvg | null {
  const complete = /<\/svg\s*>\s*$/i.test(svgChunk.trim())

  // 1) 去注释（保留合法内容）。
  let text = svgChunk.replace(/<!--[\s\S]*?-->/g, '')

  // 2) 截掉残缺尾部。
  text = cutPartialTail(text)

  // 3) 丢掉被禁标签的整段。
  text = dropBlockedElements(text, image)

  const rootChildren: SvgChild[] = []
  const stack: Array<{ tag: string; children: SvgChild[] }> = []
  let rootTag: SvgNode | null = null
  let repaired = false
  // 第二个 <svg> 整棵丢掉，用深度计数保证内部标签不会污染第一棵树。
  let discard = 0

  const current = () => (stack.length ? stack[stack.length - 1]!.children : rootChildren)

  const pushText = (raw: string) => {
    // 纯缩进/换行不产生文本节点，否则每个元素后面都挂一个空白 Text，
    // 增量补丁的比较会一直「相等但无意义」。
    if (!raw.trim()) return
    if (image) raw = decodeXmlText(raw)
    const last = current()
    const prev = last[last.length - 1]
    // 同一段文本被 token 切开时直接接上，避免逐字插入 Text 节点。
    if (prev && prev.kind === 'text') prev.text += raw
    else last.push({ kind: 'text', text: raw })
  }

  let i = 0
  while (i < text.length) {
    const lt = text.indexOf('<', i)
    if (lt < 0) {
      pushText(text.slice(i))
      break
    }
    if (lt > i) pushText(text.slice(i, lt))
    if (text.startsWith('<!--', lt)) {
      const close = text.indexOf('-->', lt)
      i = close < 0 ? text.length : close + 3
      continue
    }
    if (text.startsWith('<?', lt) || text.startsWith('<!', lt)) {
      const close = text.indexOf('>', lt)
      i = close < 0 ? text.length : close + 1
      continue
    }

    // 找配对的 `>`，尊重引号。
    let j = lt + 1
    let quote: string | null = null
    while (j < text.length) {
      const ch = text[j]!
      if (quote) {
        if (ch === quote) quote = null
      } else if (ch === '"' || ch === "'") {
        quote = ch
      } else if (ch === '>') {
        break
      }
      j += 1
    }
    if (j >= text.length) {
      // 标签没写完 —— 到此为止。
      repaired = true
      break
    }

    const tagText = text.slice(lt, j + 1)
    i = j + 1

    const isClose = tagText[1] === '/'
    const nameMatch = NAME_RE.exec(tagText.slice(isClose ? 2 : 1))
    if (!nameMatch) {
      repaired = true
      continue
    }
    const name = nameMatch[1]!
    const lower = name.toLowerCase()

    if ((BLOCKED_TAGS.has(lower) && !(image && IMAGE_TAGS.has(lower))) || lower.includes(':')) {
      repaired = true
      continue
    }

    if (isClose) {
      if (discard > 0) {
        discard -= 1
        continue
      }
      if (lower === 'svg' && stack.length === 0) break
      const at = stack.map((frame) => frame.tag).lastIndexOf(lower)
      if (at < 0) {
        repaired = true
        continue
      }
      // 栈顶不是被关的那个，说明中间还夹着没闭合的祖先 —— 那才算「补齐」。
      if (stack.length - 1 !== at) repaired = true
      stack.length = at
      continue
    }

    const selfClosing = /\/\s*>$/.test(tagText)
    const rawAttrs = rawAttrsOf(tagText, name, selfClosing)
    const safeRaw = hasDangerousUrl(rawAttrs)
      ? rawAttrs.replace(/\b(?:href|xlink:href)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '')
      : rawAttrs
    const attrs = parseAttrs(safeRaw, lower, image)
    const node: SvgNode = { kind: 'element', tag: name, attrs, children: [] }

    if (discard > 0) {
      if (!selfClosing) discard += 1
      continue
    }

    if (lower === 'svg' && stack.length === 0) {
      if (rootTag) {
        // 第二个 <svg>：整棵跳过，别把两棵树揉在一起。
        repaired = true
        if (!selfClosing) discard = 1
        continue
      }
      rootTag = node
      if (!selfClosing) stack.push({ tag: lower, children: rootChildren })
      continue
    }

    if (!rootTag) {
      // <svg> 之前的游离节点（HTML 尾巴等）没有容器，丢掉。
      repaired = true
      continue
    }

    current().push(node)
    // 样式按原文保留为 XML 文本，不能把 CSS 里的 < 当成图元。
    if (image && lower === 'style' && !selfClosing) {
      const close = /<\/style\s*>/i.exec(text.slice(i))
      const end = close ? i + close.index : text.length
      const raw = text.slice(i, end)
      const cdata = /^\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*$/.exec(raw)
      const css = cdata ? cdata[1]! : decodeXmlText(raw)
      node.children.push({kind: 'text', text: css})
      i = close ? end + close[0].length : text.length
      if (!close) repaired = true
      continue
    }
    if (!selfClosing) {
      stack.push({ tag: lower, children: node.children })
    }
  }

  if (!rootTag) return null
  if (stack.length) repaired = true
  // 收尾时把栈里剩下的标签都算作「被我们补齐了」。
  rootTag.children = rootChildren

  const markup = `<${rootTag.tag}${serializeAttrs(rootTag.attrs)}>${serialize(rootTag.children)}</${rootTag.tag}>`

  const rootTagText = `<${rootTag.tag}${serializeAttrs(rootTag.attrs)}>`
  const viewBox = firstAttr(rootTagText, 'viewBox')
  // 模型画的图绝大多数只给 viewBox 不给 width/height，尺寸回退到 viewBox，
  // 否则图框右上角的「800×400」永远是空的。
  const box = viewBox ? viewBox.split(/[\s,]+/).map(Number) : null
  const boxW = box && box.length === 4 && Number.isFinite(box[2]) ? box[2]! : null
  const boxH = box && box.length === 4 && Number.isFinite(box[3]) ? box[3]! : null
  const width = parseLength(rootTag.attrs.width ?? firstAttr(rootTagText, 'width')) ?? boxW
  const height = parseLength(rootTag.attrs.height ?? firstAttr(rootTagText, 'height')) ?? boxH

  return {
    markup,
    root: rootTag,
    viewBox,
    width,
    height,
    repaired,
    // 从最终树里数，而不是从解析过程里累加：被丢弃的子树不会算进去。
    drawable: countDrawable(rootTag.children),
    complete,
  }
}

/* ============================ 增量补丁 ============================ */

const SVG_NS = 'http://www.w3.org/2000/svg'

function syncAttrs(el: Element, attrs: SvgAttrs): void {
  const wanted = new Set(Object.keys(attrs))
  for (const existing of Array.from(el.attributes)) {
    if (!wanted.has(existing.name)) el.removeAttribute(existing.name)
  }
  for (const [name, value] of Object.entries(attrs)) {
    if (el.getAttribute(name) !== value) el.setAttribute(name, value)
  }
}

function createChild(doc: Document, child: SvgChild): ChildNode {
  if (child.kind === 'text') return doc.createTextNode(child.text)
  const el = doc.createElementNS(SVG_NS, child.tag)
  syncAttrs(el, child.attrs)
  for (const grand of child.children) el.appendChild(createChild(doc, grand))
  return el
}

function syncChildren(parent: Element, children: SvgChild[]): void {
  const doc = parent.ownerDocument
  if (!doc) return
  const existing = Array.from(parent.childNodes)

  for (let index = 0; index < children.length; index += 1) {
    const want = children[index]!
    const cur = existing[index]
    if (!cur) {
      parent.appendChild(createChild(doc, want))
      continue
    }
    if (want.kind === 'text') {
      if (cur.nodeType === 3) {
        if (cur.nodeValue !== want.text) cur.nodeValue = want.text
      } else {
        parent.replaceChild(doc.createTextNode(want.text), cur)
      }
      continue
    }
    const curEl = cur.nodeType === 1 ? (cur as Element) : null
    if (
      !curEl ||
      curEl.namespaceURI !== SVG_NS ||
      curEl.tagName.toLowerCase() !== want.tag.toLowerCase()
    ) {
      parent.replaceChild(createChild(doc, want), cur)
      continue
    }
    syncAttrs(curEl, want.attrs)
    syncChildren(curEl, want.children)
  }

  // 模型极少回退，但脏数据/重写时长度会变短。
  for (let index = existing.length - 1; index >= children.length; index -= 1) {
    const node = existing[index]
    if (node) parent.removeChild(node)
  }
}

/** 多张图可复用 gradient、marker 等原始 ID，渲染时按实例隔离引用。 */
export function scopeSvg(parsed: ParsedSvg | null, scope: string): ParsedSvg | null {
  if (!parsed?.root) return parsed
  const ids = new Map<string, string>()
  const collect = (node: SvgNode) => {
    if (node.attrs.id) ids.set(node.attrs.id, `${scope}-${node.attrs.id}`)
    for (const child of node.children) if (child.kind === 'element') collect(child)
  }
  collect(parsed.root)
  const clone = (node: SvgNode): SvgNode => ({...node, attrs: Object.fromEntries(Object.entries(node.attrs).map(([key, raw]) => {
    const value = key === 'id' ? ids.get(raw) ?? raw :
      (key === 'aria-labelledby' || key === 'aria-describedby') ? raw.split(/\s+/).map(id => ids.get(id) ?? id).join(' ') :
      (key === 'href' || key === 'xlink:href') && raw.startsWith('#') ? `#${ids.get(raw.slice(1)) ?? raw.slice(1)}` :
      raw.replace(/url\(\s*(['"]?)#([^\s)'"\r\n]+)\1\s*\)/g, (_, _quote, id) => `url(#${ids.get(id) ?? id})`)
    return [key, value]
  })), children: node.children.map(child => child.kind === 'element' ? clone(child) : {...child})})
  const root = clone(parsed.root)
  return {...parsed, root, markup: serialize([root])}
}

/**
 * 把节点树就地写进宿主元素，返回根 `<svg>`。
 *
 * 宿主必须是 React 不管 children 的空元素（否则 React 下一次渲染会把
 * 我们插进去的节点冲掉）。首次调用会建树，之后每次只碰变化的那几个节点。
 */
export function patchSvg(host: HTMLElement, root: SvgNode | null): SVGSVGElement | null {
  if (!root || root.tag.toLowerCase() !== 'svg') return null
  const doc = host.ownerDocument
  if (!doc) return null

  const current = host.firstElementChild as Element | null
  const reusable =
    current &&
    current.namespaceURI === SVG_NS &&
    current.tagName.toLowerCase() === 'svg' &&
    host.childNodes.length === 1

  const target = reusable ? current : doc.createElementNS(SVG_NS, 'svg')
  if (!reusable) {
    host.textContent = ''
    host.appendChild(target)
  }
  syncAttrs(target, root.attrs)
  syncChildren(target, root.children)
  // 补丁落完后给「刚出现」的图元补一遍绘制动画；已有节点不受影响。
  strokePrep(host)
  return target as SVGSVGElement
}

/* ============================ 绘制动画 ============================ */

/**
 * 「描边生长 → 填色淡入 → 文字淡入」的绘制动画层。
 *
 * 只在**新节点**上播放：增量补丁里只有 createChild 真正建出来的元素未被
 * 标记；已有节点随流更新属性（路径变长、坐标微调）不重新播，否则长路径
 * 每来一个 token 就闪一遍。
 *
 * 动画走 Web Animations API 而不是改 attribute —— syncAttrs 会把模型没写
 * 的属性全部收回，挂在 attribute 上的 dasharray/opacity 活不过下一次补丁；
 * WAAPI 的临时覆盖值不落在 DOM 属性上，播完自动落回模型自己的样式。
 */

const drawnMarks = new WeakMap<Element, WeakSet<Element>>()

function motionReduced(): boolean {
  return (
    typeof matchMedia === 'function' &&
    matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

function clampNumber(lo: number, value: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value))
}

/** 播一次即销：getAnimations() 不堆积已结束记录，调用方据此确认画面已稳定。 */
function playOnce(el: Element, frames: Keyframe[], options: KeyframeAnimationOptions): void {
  const target = el as HTMLElement
  if (typeof target.animate !== 'function') return
  const animation = target.animate(frames, options)
  animation.finished.then(() => animation.cancel(), () => {})
}

const STAGGER_MS = 90
const STAGGER_CAP_MS = 1080

function staggerDelay(step: number): number {
  // 流式期每次补丁通常只新增一两个节点（step≈0），延迟≈0；
  // 整图一次性上屏（历史回放、短图）时才形成级联，封顶上屏节奏不拖沓。
  return Math.min(step * STAGGER_MS, STAGGER_CAP_MS)
}

function animateShape(el: SVGGeometryElement, step: number): void {
  if (typeof el.animate !== 'function') return
  const view = el.ownerDocument?.defaultView
  const cs = view && typeof view.getComputedStyle === 'function' ? view.getComputedStyle(el) : null
  const delay = staggerDelay(step)
  const stroke = cs ? cs.stroke : el.getAttribute('stroke')
  const dashed = cs ? cs.strokeDasharray !== 'none' : /dasharray/.test(el.getAttribute('style') ?? '')
  let length = 0
  if (stroke && stroke !== 'none' && !dashed && typeof el.getTotalLength === 'function') {
    try {
      length = el.getTotalLength()
    } catch {
      length = 0
    }
  }
  if (length > 1) {
    // 描边生长：dasharray 只存在于动画期间，播完落回模型自己的描边。
    const duration = clampNumber(280, length * 1.6, 850)
    playOnce(el,
      [
        { strokeDasharray: `${length}`, strokeDashoffset: `${length}` },
        { strokeDasharray: `${length}`, strokeDashoffset: '0' },
      ],
      { duration, delay, easing: 'ease-out' },
    )
    const fill = cs ? cs.fill : el.getAttribute('fill')
    if (fill && fill !== 'none') {
      playOnce(el,
        [{ fillOpacity: '0' }, { fillOpacity: cs?.fillOpacity ?? '1' }],
        { duration: 420, delay: delay + duration * 0.55, easing: 'ease-out' },
      )
    }
    return
  }
  // 无描边色块、虚线连接：直接淡入。
  playOnce(el,
    [{ opacity: '0' }, { opacity: cs?.opacity ?? '1' }],
    { duration: 380, delay, easing: 'ease-out' },
  )
}

function animateLabel(el: Element, step: number): void {
  playOnce(el,
    [{ opacity: '0' }, { opacity: '1' }],
    { duration: 350, delay: staggerDelay(step) + 120, easing: 'ease-out' },
  )
}

/**
 * 给宿主里**尚未画过**的图元各播一遍绘制动画；`replay` 清掉标记整图重播。
 * 流式期由 patchSvg 在每次增量补丁后调用；测试环境（无 WAAPI）安全 no-op。
 */
export function strokePrep(host: HTMLElement, replay = false): void {
  const svg = host.firstElementChild
  if (!svg || svg.namespaceURI !== SVG_NS || svg.tagName.toLowerCase() !== 'svg') return
  if (motionReduced()) return
  let marks = drawnMarks.get(svg)
  if (!marks || replay) {
    marks = new WeakSet()
    drawnMarks.set(svg, marks)
  }
  const marked = marks
  let step = 0
  const walk = (el: Element) => {
    const tag = el.tagName.toLowerCase()
    if (!marked.has(el)) {
      if (DRAWABLE.has(tag)) {
        marked.add(el)
        animateShape(el as SVGGeometryElement, step)
        step += 1
      } else if (tag === 'text' || tag === 'tspan') {
        marked.add(el)
        animateLabel(el, step)
        step += 1
      }
    }
    for (const child of Array.from(el.children)) walk(child)
  }
  walk(svg)
}
