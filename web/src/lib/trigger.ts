import { listVisibleCount } from './listPagination'
/**
 * 输入框内联触发器 —— 「输入即操作」的核心。
 *
 * 支持键盘内联操作：在输入框里打
 * 一个 `@` 或 `/`，就地唤起候选，方向键选、回车落。
 *
 * 本文件是纯函数核心（无 DOM、无 React），因为这一层最容易出错且最难靠肉眼验：
 * 光标在哪儿算触发、什么上下文不该触发、模糊匹配怎么打分、补全后光标落哪。
 * 判定逻辑一旦含糊，输入框就会开始「误弹」或「该弹不弹」——这两者都是
 * 让用户立刻放弃使用的原因，比没有这个功能更糟。
 */

/** 触发器大类。`@` 出引用，`/` 出命令。 */
export type TriggerKind = 'reference' | 'command'

/** 候选的细分类，决定图标与分组标题。 */
export type CandidateKind = 'file' | 'directory' | 'session' | 'skill' | 'command'

export interface TriggerCandidate {
  /** 选中后写入输入框的字面量。 */
  insert: string
  /** 列表主标题。 */
  title: string
  /** 次要说明：路径 / 描述。 */
  hint?: string
  kind: CandidateKind
}

export interface TriggerState {
  kind: TriggerKind
  /** 触发符之后、到光标为止的查询串。 */
  query: string
  /** 触发符本身所在下标；替换区间是 [from, to)。 */
  from: number
  to: number
}

const TRIGGERS: Record<string, TriggerKind> = { '@': 'reference', '/': 'command' }

/** 围栏必须以相同字符、至少相同长度闭合；行内代码按反引号串长度配对。 */
export function isInCodeContext(text: string, caret: number): boolean {
  const lines = text.slice(0, clamp(caret, 0, text.length)).split('\n')
  let fence: { char: string; length: number } | null = null
  for (const line of lines.slice(0, -1)) {
    const match = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line)
    if (!match) continue
    const marker = match[1]!
    if (fence) {
      if (marker[0] === fence.char && marker.length >= fence.length && /^\s*$/.test(match[2]!)) fence = null
    } else if (marker[0] !== '`' || !match[2]!.includes('`')) {
      fence = { char: marker[0]!, length: marker.length }
    }
  }
  if (fence) return true
  const line = lines[lines.length - 1] ?? ''
  let delimiter = 0
  for (const match of line.matchAll(/`+/g)) {
    // 代码内部的反斜杠是正文；外部才支持转义开定界符。
    if (!delimiter) {
      let slashes = 0
      for (let i = match.index - 1; i >= 0 && line[i] === '\\'; i -= 1) slashes += 1
      if (slashes % 2) continue
      delimiter = match[0].length
    } else if (match[0].length === delimiter) delimiter = 0
  }
  return delimiter !== 0
}

function clamp(value: number, lo: number, hi: number): number {
  if (!Number.isFinite(value)) return lo
  return Math.min(Math.max(Math.trunc(value), lo), hi)
}

/**
 * 检测光标处是否处于触发状态。
 *
 * 关键简化：**触发符必须是当前「空白分隔词」的首字符**。这一条同时解决了
 * `and/or`（不以 / 开头）、`a@b.com`（不以 @ 开头）两类最常见的误触发，
 * 不用再维护一串前缀黑名单。
 */
export function detectTrigger(text: string, caret: number): TriggerState | null {
  const at = clamp(caret, 0, text.length)
  if (at === 0) return null

  // 回退到当前词的起点：遇空白即停。
  let start = at
  while (start > 0 && !/\s/.test(text[start - 1]!)) start -= 1
  if (start >= at) return null

  const head = text[start]!
  const kind = TRIGGERS[head]
  if (!kind) return null

  // 查询串里不允许空白 —— 一旦用户打了空格就说明这个触发已经结束，
  // 继续弹框反而挡路。
  const query = text.slice(start + 1, at)
  if (/\s/.test(query)) return null

  // 命令里出现斜杠更像路径（/api/v1），不当作命令。
  if (kind === 'command' && query.includes('/')) return null

  if (isInCodeContext(text, at)) return null

  return { kind, query, from: start, to: at }
}

/* ============================ 模糊匹配 ============================ */

export interface ScoredCandidate {
  candidate: TriggerCandidate
  score: number
  /** 主标题上被命中的下标集合，用于高亮。 */
  hits: number[]
}

/** 子序列匹配：query 的字符按序出现在 target 中。返回命中下标或 null。 */
function subsequence(target: string, query: string): number[] | null {
  const hits: number[] = []
  let ti = 0
  for (let qi = 0; qi < query.length; qi += 1) {
    const ch = query[qi]!
    let found = -1
    while (ti < target.length) {
      if (target[ti]!.toLowerCase() === ch.toLowerCase()) {
        found = ti
        ti += 1
        break
      }
      ti += 1
    }
    if (found < 0) return null
    hits.push(found)
  }
  return hits
}

/** 大小写转换可能扩展字符；高亮始终映射回原始 UTF-16 下标。 */
function foldedTitle(value: string): { text: string; offsets: number[][] } {
  const text = value.toLowerCase()
  let offset = 0
  const offsets: number[][] = []
  for (const character of value) {
    const folded = character.toLowerCase()
    const original = Array.from({ length: character.length }, (_, i) => offset + i)
    for (let i = 0; i < folded.length; i += 1) offsets.push(original)
    offset += character.length
  }
  return { text, offsets }
}

/** 连续命中的加成：连着写比跳着写更像用户本意。 */
function compactness(hits: number[]): number {
  if (hits.length < 2) return 0
  let gaps = 0
  for (let i = 1; i < hits.length; i += 1) gaps += hits[i]! - hits[i - 1]! - 1
  return Math.max(0, 20 - gaps * 2)
}

/** 最后一段（文件名）比整条路径更可能是用户心里在想的东西。 */
function tail(value: string): string {
  const cut = Math.max(value.lastIndexOf('/'), value.lastIndexOf('\\'))
  return cut < 0 ? value : value.slice(cut + 1)
}

/**
 * 给单个候选打分。null = 不匹配。
 *
 * 权重是有意分层而不是线性相似度：精确命中 > 前缀命中 > 文件名前缀 > 散字符，
 * 因为用户打 `@pro` 想要的一定是 `progressiveSvg.ts`，而不是某个路径里恰好
 * 出现过 p-r-o 的文件。
 */
export function scoreCandidate(candidate: TriggerCandidate, query: string): ScoredCandidate | null {
  const q = query.trim().toLowerCase()
  if (!q) return { candidate, score: 100, hits: [] }

  const title = candidate.title
  const folded = foldedTitle(title)
  const titleLower = folded.text
  const originalHits = (hits: number[]) => [...new Set(hits.flatMap((index) => folded.offsets[index] ?? []))]

  if (titleLower === q) return { candidate, score: 1000, hits: range(title.length) }

  if (titleLower.startsWith(q)) {
    return { candidate, score: 800 - Math.min(title.length - q.length, 99), hits: originalHits(range(q.length)) }
  }

  const name = tail(title)
  if (name.toLowerCase().startsWith(q)) {
    return { candidate, score: 700, hits: originalHits(range(titleLower.length - name.toLowerCase().length, titleLower.length - name.toLowerCase().length + q.length)) }
  }

  const sub = subsequence(titleLower, q)
  if (sub) return { candidate, score: 500 + compactness(sub), hits: originalHits(sub) }

  const hintHits = candidate.hint ? subsequence(candidate.hint.toLowerCase(), q) : null
  if (hintHits) return { candidate, score: 300, hits: [] }

  return null
}

function range(from: number, to?: number): number[] {
  const end = to ?? from
  const out: number[] = []
  for (let i = to === undefined ? 0 : from; i < end; i += 1) out.push(i)
  return out
}

/** 过滤 + 排序 + 截断。空查询时保持原顺序（目录优先于文件、命令优先）。 */
export function rankCandidates(
  candidates: TriggerCandidate[],
  query: string,
  limit = 40,
): ScoredCandidate[] {
  const scored: ScoredCandidate[] = []
  for (const candidate of candidates) {
    const hit = scoreCandidate(candidate, query)
    if (hit) scored.push(hit)
  }
  scored.sort((a, b) => (b.score - a.score) || a.candidate.title.localeCompare(b.candidate.title))
  return scored.slice(0, limit)
}

/* ============================ 落词 ============================ */

/** 选中后写入的内容：字面量 + 一个空格，把菜单顺势关掉。 */
export function completionText(candidate: TriggerCandidate): string {
  return candidate.insert + ' '
}

/**
 * 用候选替换触发区间。
 *
 * 末尾那个空格是刻意的：补全完成后若光标紧贴候选末尾，用户接着打的下一个字符
 * 会立刻重新触发，菜单「闪一下又弹出来」——这是这类交互最招人烦的细节。
 */
export function applyCompletion(
  text: string,
  candidate: TriggerCandidate,
  state: Pick<TriggerState, 'from' | 'to'>,
): { text: string; caret: number } {
  const from = clamp(state.from, 0, text.length)
  const to = clamp(state.to, from, text.length)
  const insert = completionText(candidate)
  const next = text.slice(0, from) + insert + text.slice(to)
  return { text: next, caret: from + insert.length }
}

/** 分类后独立分页，保证显示顺序与键盘选择顺序一致。 */
export const CANDIDATE_KINDS: CandidateKind[] = ['directory', 'file', 'skill', 'session', 'command']
export const REFERENCE_PAGE_SIZE = 5
export function groupedCandidates(items: ScoredCandidate[], limits: Partial<Record<CandidateKind, number>> = {}): ScoredCandidate[] {
  return CANDIDATE_KINDS.flatMap((kind) => {
    const group = items.filter((item) => item.candidate.kind === kind)
    return group.slice(0, listVisibleCount(group.length, limits[kind] ?? REFERENCE_PAGE_SIZE))
  })
}
