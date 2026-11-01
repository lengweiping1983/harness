import { t, useLocale } from '@/i18n'
import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronRight, Copy, WrapText } from 'lucide-react'
import { cn, copyText } from '@/lib/utils'
import { useAppStore } from '@/store/app'
import {
  extractFileDiff,
  languageFromPath,
  pairRowsForWords,
  createWordDiffBudget,
  type DiffSegment,
  type ParsedDiff,
  type PairedRow,
} from '@/lib/diff'
import { highlightToHtml, isHighlightable } from '@/lib/highlight'
import { IconButton, Tooltip } from '@/components/ui/primitives'

/**
 * 文件改动 diff 视图。
 *
 * 将工具结果中的统一差异渲染为文件修改视图。
 *
 * - 双栏行号（旧 / 新），一眼定位到文件位置；
 * - 行级 +/- 着色 + **词级加深**，把"这一行到底改了哪个词"标出来；
 * - 未改动的上下文行走语法高亮，改动行走词级标记——和 GitHub 的视觉分层一致，
 *   两套着色不会互相打架；
 * - 超过阈值默认折叠，避免一次改几百行把对话流撑爆。
 */

/** 默认渲染的行数上限；超出的部分折叠，点开再放。 */
const COLLAPSE_THRESHOLD = 160

function SegmentText({ segments }: { segments: DiffSegment[] }) {
  useLocale()

  return (
    <>
      {segments.map((seg, index) =>
        seg.changed ? (
          <mark
            key={index}
            className="rounded-sm bg-ink/[0.12] px-px text-inherit [color:inherit]"
          >
            {seg.text}
          </mark>
        ) : (
          <span key={index}>{seg.text}</span>
        ),
      )}
    </>
  )
}

const DiffRows = memo(function DiffRows({
  rows,
  wrap,
  html,
}: {
  rows: PairedRow[]
  wrap: boolean
  html: Map<number, string>
}) {
  useLocale()

  return (
    <>
      {rows.map(({ row, segments }, index) => {
        const tone =
          row.kind === 'add'
            ? 'bg-ok/[0.12] text-ok'
            : row.kind === 'del'
              ? 'bg-danger/[0.12] text-danger'
              : 'text-muted'
        const marker = row.kind === 'add' ? '+' : row.kind === 'del' ? '−' : ' '
        const marked = html.get(index)
        return (
          <tr key={index} className={cn('align-top', row.kind !== 'context' && tone)}>
            <td className="diff-gutter select-none text-end text-faint/60">{row.oldNo ?? ''}</td>
            <td className="diff-gutter select-none text-end text-faint/60">{row.newNo ?? ''}</td>
            <td className="diff-gutter select-none text-center text-faint/70">{marker}</td>
            <td className={cn('font-mono', wrap ? 'whitespace-pre-wrap break-words' : 'whitespace-pre')}>
              {segments ? (
                <SegmentText segments={segments} />
              ) : marked ? (
                <span className="hljs" dangerouslySetInnerHTML={{ __html: marked }} />
              ) : (
                row.text
              )}
            </td>
          </tr>
        )
      })}
    </>
  )
})

export interface DiffViewProps {
  output: unknown
  /**
   * 工具的结构化结果。后端把 unified diff 放在这里（`display.diff`），
   * 而 `output` 通常已经是回传给模型的纯文本——两者都要试。
   */
  display?: unknown
  /** 文件路径，用来挑高亮语言。 */
  path?: string | null
  className?: string
}

/**
 * 工具卡片里的 diff 区块。
 *
 * `extractFileDiff` 返回 null（这个工具压根不改文件，或者改动无法还原成
 * diff）时整块不渲染，由调用方回退到通用 JSON / 文本视图——比硬塞一个空
 * diff 框诚实。
 */
export const DiffView = memo(function DiffView({
  output,
  display,
  path,
  className,
}: DiffViewProps) {
  const locale = useLocale()

  // 结构化差异包含执行后的真实行号和上下文。
  const evidence = useMemo<{parsed: ParsedDiff; text: string} | null>(() => {
    for (const source of [display, output]) {
      const parsed = extractFileDiff(source)
      if (parsed) return {parsed, text: (source as {diff: string}).diff}
    }
    return null
  }, [output, display, locale])
  const parsed = evidence?.parsed ?? null
  const [expanded, setExpanded] = useState(false)
  const [wrap, setWrap] = useState(false)
  const [copied, setCopied] = useState(false)
  const copyVersion = useRef(0)
  const copyTimer = useRef<ReturnType<typeof setTimeout>>()
  const [html, setHtml] = useState<Map<number, string>>(() => new Map())

  const language = useMemo(() => languageFromPath(path ?? ''), [path, locale])

  /** 铺平所有 hunk：行级着色已经足够表达语义，hunk 边界只保留在复制文本里。 */
  const paired = useMemo(() => {
    if (!parsed) return []
    const budget = createWordDiffBudget()
    return parsed.hunks.flatMap((hunk) => pairRowsForWords(hunk.rows, budget))
  }, [parsed, locale])

  // 只给上下文行上语法高亮。改动行已经走了词级标记，再叠一层 hljs 的
  // span 配色会让"改了哪个词"读不出来。语言不认识就整块不请求。
  useEffect(() => {
    if (!parsed || !language || !isHighlightable(language)) return
    let alive = true
    const pending: Array<{ index: number; text: string }> = []
    let index = 0
    for (const hunk of parsed.hunks) {
      for (const row of hunk.rows) {
        if (row.kind === 'context') pending.push({ index, text: row.text })
        index += 1
      }
    }
    void Promise.all(
      pending.map(async (item) => ({ index: item.index, value: await highlightToHtml(item.text, language) })),
    ).then((results) => {
      if (!alive) return
      const next = new Map<number, string>()
      for (const item of results) if (item.value) next.set(item.index, item.value)
      if (next.size) setHtml(next)
    })
    return () => {
      alive = false
    }
  }, [parsed, language])

  // 换工具卡片时把折叠状态和着色缓存一起清掉。
  useEffect(() => {
    copyVersion.current++
    clearTimeout(copyTimer.current)
    setCopied(false)
    setExpanded(false)
    setHtml(new Map())
    return () => {
      copyVersion.current++
      clearTimeout(copyTimer.current)
    }
  }, [display, output])

  if (!parsed || !parsed.ok) return null

  const total = parsed.additions + parsed.deletions
  const hasOverflow = paired.length > COLLAPSE_THRESHOLD
  const collapsed = !expanded && hasOverflow
  const shown = collapsed ? paired.slice(0, COLLAPSE_THRESHOLD) : paired
  const fileName = path ?? parsed.files[0]?.to?.replace(/^b\//, '') ?? ''

  const copy = async () => {
    if (!evidence) return
    const version = ++copyVersion.current
    clearTimeout(copyTimer.current)
    setCopied(false)
    const ok = await copyText(evidence.text)
    if (version !== copyVersion.current) return
    if (ok) {
      setCopied(true)
      copyTimer.current = setTimeout(() => setCopied(false), 1600)
    } else useAppStore.getState().toast({title: t('ui.components.chat.blocks.MessageActions.753d8bb0da99'),
      description: t('ui.components.chat.blocks.MessageActions.bc3be7231933'), tone: 'danger'})
  }

  return (
    <figure className={cn('diff-view overflow-hidden rounded-lg border border-line', className)}>
      <figcaption className="flex h-8 items-center gap-2.5 border-b border-line bg-raised/60 px-2.5">
        <span className="font-mono text-2xs font-medium text-ok">+{parsed.additions}</span>
        <span className="font-mono text-2xs font-medium text-danger">−{parsed.deletions}</span>
        {fileName ? (
          <span className="truncate font-mono text-2xs text-faint" title={fileName}>
            {fileName.split(/[\\/]/).pop()}
          </span>
        ) : null}
        <div className="ml-auto flex items-center gap-0.5">
          <Tooltip content={wrap ? t("ui.components.chat.blocks.DiffView.8180fd1d996c") : t("ui.components.chat.blocks.DiffView.b096842c67b2")}>
            <IconButton
              label={wrap ? t("ui.components.chat.blocks.DiffView.8180fd1d996c") : t("ui.components.chat.blocks.DiffView.b096842c67b2")}
              size="xs"
              active={wrap}
              onClick={() => setWrap((v) => !v)}
            >
              <WrapText className="h-3.5 w-3.5" />
            </IconButton>
          </Tooltip>
          <Tooltip content={copied ? t("ui.components.chat.blocks.DiffView.8f6f8d979c98") : t("ui.components.chat.blocks.DiffView.cbf9016bb88f")}>
            <IconButton label={copied ? t("ui.components.chat.blocks.DiffView.8f6f8d979c98") : t("ui.components.chat.blocks.DiffView.cbf9016bb88f")} size="xs" onClick={copy}>
              {copied ? <Check className="h-3.5 w-3.5 text-ok" /> : <Copy className="h-3.5 w-3.5" />}
            </IconButton>
          </Tooltip>
        </div>
      </figcaption>
      <div className="max-h-diff overflow-auto bg-surface/60">
        <table className="w-full border-collapse">
          <tbody>
            <DiffRows rows={shown} wrap={wrap} html={html} />
          </tbody>
        </table>
      </div>
      {hasOverflow ? (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="flex w-full items-center justify-center gap-1.5 border-t border-line bg-raised/40 py-1.5 text-2xs text-faint transition-colors hover:bg-raised/70 hover:text-muted"
        >
          <ChevronRight aria-hidden className={cn('h-3 w-3 transition-transform', expanded && 'rotate-90')} />
          {expanded ? t("ui.components.chat.blocks.DiffView.afd4b783536b") : t("ui.components.chat.blocks.DiffView.dd8520dac70d", {p0: (paired.length - COLLAPSE_THRESHOLD), p1: (total)})}
        </button>
      ) : null}
    </figure>
  )
})
