/**
 * 统一 diff 的解析与词级比对。
 *
 * 解析工具结果中的统一差异，保留真实行号与上下文。
 *
 * 全部是纯函数，不碰 DOM，可以直接单测。
 */

/* ============================ 类型 ============================ */

export type DiffRowKind = 'context' | 'add' | 'del' | 'meta'

export interface DiffRow {
  kind: DiffRowKind
  /** 行文本，不含 unified diff 的前缀字符。 */
  text: string
  /** 旧文件行号；新增行为 null。 */
  oldNo: number | null
  /** 新文件行号；删除行为 null。 */
  newNo: number | null
}

export interface DiffHunk {
  header: string
  oldStart: number
  oldLines: number
  newStart: number
  newLines: number
  rows: DiffRow[]
}

export interface DiffFileRef {
  from: string
  to: string
}

export interface ParsedDiff {
  files: DiffFileRef[]
  hunks: DiffHunk[]
  additions: number
  deletions: number
  /** 是否解析出了任何内容——false 时调用方应回退到通用渲染。 */
  ok: boolean
}

/* ============================ 解析 unified diff ============================ */

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/

/**
 * 解析 unified diff。
 *
 * 容忍度按"这是给人看的 UI 数据"来定：认识 `diff --git` / `---` / `+++`
 * 这类元信息行，遇到看不懂的行直接当 `meta` 收下而不是抛错——后端
 * difflib 的输出细节会随 Python 版本变化，UI 不该因为多一行前缀就整个崩掉。
 */
export function parseUnifiedDiff(input: string): ParsedDiff {
  const result: ParsedDiff = { files: [], hunks: [], additions: 0, deletions: 0, ok: false }
  if (typeof input !== 'string') return result
  const text = input.replace(/\r\n/g, '\n')
  if (!text.trim()) return result

  let hunk: DiffHunk | null = null
  let oldNo = 0
  let newNo = 0
  let pendingFrom: string | null = null

  for (const raw of text.split('\n')) {
    const hunkMatch = HUNK_RE.exec(raw)
    if (hunkMatch) {
      oldNo = Number(hunkMatch[1])
      newNo = Number(hunkMatch[3])
      const oldCount = hunkMatch[2] === undefined ? 1 : Number(hunkMatch[2])
      const newCount = hunkMatch[4] === undefined ? 1 : Number(hunkMatch[4])
      hunk = {
        header: raw,
        oldStart: oldNo,
        oldLines: oldCount,
        newStart: newNo,
        newLines: newCount,
        rows: [],
      }
      result.hunks.push(hunk)
      continue
    }

    // 文件头要出现在所属 hunk 之前，所以先记着，遇到 @@ 再提交。
    const inHunk = hunk !== null && (oldNo < hunk.oldStart + hunk.oldLines || newNo < hunk.newStart + hunk.newLines)
    if (!inHunk && raw.startsWith('--- ')) {
      pendingFrom = raw.slice(4).trim()
      continue
    }
    if (!inHunk && raw.startsWith('+++ ')) {
      result.files.push({ from: pendingFrom ?? '', to: raw.slice(4).trim() })
      pendingFrom = null
      continue
    }
    if (raw.startsWith('diff --git') || raw.startsWith('index ') || raw.startsWith('new file mode') || raw.startsWith('deleted file mode') || raw.startsWith('similarity index') || raw.startsWith('rename from') || raw.startsWith('rename to')) {
      continue
    }
    if (raw.startsWith('Binary files') || raw.startsWith('GIT binary patch')) {
      continue
    }

    if (!hunk) continue
    // 补丁末尾的 LF 不代表一个额外的上下文行。
    if (raw === '') continue

    if (raw.startsWith('+')) {
      hunk.rows.push({ kind: 'add', text: raw.slice(1), oldNo: null, newNo: newNo++ })
      result.additions += 1
    } else if (raw.startsWith('-')) {
      hunk.rows.push({ kind: 'del', text: raw.slice(1), oldNo: oldNo++, newNo: null })
      result.deletions += 1
    } else if (raw.startsWith(' ')) {
      hunk.rows.push({ kind: 'context', text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ })
    } else if (raw.startsWith('\\')) {
      // "\ No newline at end of file"
      hunk.rows.push({ kind: 'meta', text: raw.slice(2), oldNo: null, newNo: null })
    }
  }

  // difflib 在"两个文本完全相同"时返回空串，这里保持 ok=false 让调用方回退。
  result.ok = result.hunks.length > 0
  return result
}

/* ============================ 词级比对 ============================ */

export interface DiffSegment {
  text: string
  changed: boolean
}

/** 把一段文本切成"词"，保留分隔符，这样拼回去等于原文。 */
function tokenize(text: string): string[] {
  return text.match(/[A-Za-z0-9_$]+|\s+|[^\sA-Za-z0-9_$]/g) ?? []
}

/** 一次差异呈现共享计算额度，词级高亮不能阻塞完整补丁的查看与复制。 */
export function createWordDiffBudget(): {cells: number} { return {cells: 1_000_000} }

/**
 * 词级差异：把 `-` 行和紧随其后的 `+` 行配对，标出真正改动的片段。
 *
 * 没有这一步的话，一行改动里没变的部分会和改掉的部分同样高亮，
 * "我到底改了哪个词"就得靠肉眼逐字比——成熟 diff 视图都会做这层。
 */
export function diffWords(before: string, after: string, budget = createWordDiffBudget()): { left: DiffSegment[]; right: DiffSegment[] } {
  if (before === after) return {left: [{text: before, changed: false}], right: [{text: after, changed: false}]}
  const fullLine = () => ({left: before ? [{text: before, changed: true}] : [], right: after ? [{text: after, changed: true}] : []})
  if (!before || !after || budget.cells <= 0) return fullLine()
  const a = tokenize(before)
  const b = tokenize(after)

  // 超额时保留完整行级变化；不能为可选的词级装饰分配无界矩阵。
  const n = a.length
  const m = b.length
  const cells = (n + 1) * (m + 1)
  if (cells > budget.cells) return fullLine()
  budget.cells -= cells
  const table: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i -= 1) {
    const row = table[i]
    const next = table[i + 1]
    const ai = a[i]
    for (let j = m - 1; j >= 0; j -= 1) {
      row[j] = ai === b[j] ? next[j + 1] + 1 : Math.max(next[j], row[j + 1])
    }
  }

  const left: DiffSegment[] = []
  const right: DiffSegment[] = []
  const push = (out: DiffSegment[], text: string, changed: boolean) => {
    const last = out[out.length - 1]
    if (last && last.changed === changed) last.text += text
    else out.push({ text, changed })
  }

  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push(left, a[i], false)
      push(right, b[j], false)
      i += 1
      j += 1
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      push(left, a[i], true)
      i += 1
    } else {
      push(right, b[j], true)
      j += 1
    }
  }
  while (i < n) {
    push(left, a[i], true)
    i += 1
  }
  while (j < m) {
    push(right, b[j], true)
    j += 1
  }
  return { left, right }
}

/** 解析工具结果明确提供的统一差异。 */
export function extractFileDiff(output: unknown): ParsedDiff | null {
  if (output && typeof output === 'object') {
    const raw = (output as Record<string, unknown>).diff
    if (typeof raw === 'string' && raw.trim()) {
      const parsed = parseUnifiedDiff(raw)
      if (parsed.ok) return parsed
    }
  }
  return null
}

/** 从文件路径猜高亮语言；猜不到返回空串（调用方走纯文本）。 */
const EXT_LANG: Record<string, string> = {
  ts: 'typescript', tsx: 'typescript', mts: 'typescript', cts: 'typescript',
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  py: 'python', pyi: 'python', rb: 'ruby', rs: 'rust', go: 'go',
  java: 'java', kt: 'kotlin', kts: 'kotlin', scala: 'scala', swift: 'swift',
  c: 'c', h: 'c', cc: 'cpp', cpp: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp',
  cs: 'csharp', php: 'php', swift2: 'swift', sh: 'bash', bash: 'bash',
  zsh: 'bash', ps1: 'powershell', psm1: 'powershell', sql: 'sql',
  json: 'json', jsonc: 'json', yml: 'yaml', yaml: 'yaml', toml: 'ini',
  xml: 'xml', html: 'xml', svg: 'xml', vue: 'xml', css: 'css',
  scss: 'css', less: 'css', md: 'markdown', markdown: 'markdown',
  lua: 'lua', r: 'r', pl: 'perl', ex: 'elixir', exs: 'elixir',
  graphql: 'graphql', gql: 'graphql', hcl: 'ini', dockerfile: 'dockerfile',
}

export function languageFromPath(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? ''
  if (name.startsWith('Dockerfile')) return 'dockerfile'
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return ''
  return EXT_LANG[name.slice(dot + 1).toLowerCase()] ?? ''
}

/** 把 DiffRow[] 摊平成"给一对 -/+ 行做词级配对"的结果，渲染层直接消费。 */
export interface PairedRow {
  row: DiffRow
  /** 仅对 del / add 行有意义：与配对行之间的词级片段。 */
  segments: DiffSegment[] | null
}

/**
 * 把相邻的 del 行块与 add 行块两两配对，做词级高亮。
 * 多对多时按顺序配对，剩下的行不做词级标记（退化成整行着色）。
 */
export function pairRowsForWords(rows: DiffRow[], budget = createWordDiffBudget()): PairedRow[] {
  const out: PairedRow[] = rows.map((row) => ({ row, segments: null }))
  let i = 0
  while (i < rows.length) {
    if (rows[i].kind !== 'del') {
      i += 1
      continue
    }
    let j = i
    while (j < rows.length && rows[j].kind === 'del') j += 1
    const delEnd = j
    let k = j
    while (k < rows.length && rows[k].kind === 'add') k += 1
    const addEnd = k
    const delCount = delEnd - i
    const addCount = addEnd - j
    const pairs = Math.min(delCount, addCount)
    for (let p = 0; p < pairs; p += 1) {
      const { left, right } = diffWords(rows[i + p].text, rows[j + p].text, budget)
      out[i + p].segments = left
      out[j + p].segments = right
    }
    i = addEnd
  }
  return out
}
