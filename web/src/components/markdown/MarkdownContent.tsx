import { t, useLocale } from '@/i18n'
import { createContext, memo, useContext, useEffect, useId, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import { AlertCircle, FileText, Link2, Mail, RotateCw, SquareArrowOutUpRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  externalAttrs,
  isSafeExternal,
  resolveLink,
  type ResolvedLink,
  type FileLinkSource,
} from '@/lib/fileLinks'
import { relativizeToRoot } from '@/lib/deliverables'
import { headingSlug, remarkDocumentHeadings } from '@/lib/documentHeadings'
import { useAppStore } from '@/store/app'
import { Tooltip } from '@/components/ui/Tooltip'
import { LiveChart } from './LiveChart'
import { CodeBlock } from './CodeBlock'
import { RendererFrame } from './RendererFrame'
import { segmentStream, stabilizeMarkdown, type StreamSegment } from '@/lib/streaming'
import { normalizeMediaTags } from '@/lib/mediaTags'
import { remarkOutputReferences } from '@/lib/outputReferences'
import { remarkRenderer } from '@/lib/rendererRemark'
import { currentMathRenderer, loadMathRenderer } from '@/lib/mathRenderer'

export interface MarkdownFileSource { path: string; root: string | null }
const WorkspaceLinks = createContext(true)
const Streaming = createContext(false)
const DocumentSource = createContext<{ file: MarkdownFileSource; links: FileLinkSource; prefix: string } | undefined>(undefined)

/**
 * Markdown 渲染管线。
 *
 * 顺序很关键，乱了就出安全或排版问题：
 *
 *   remark-gfm  → 表格 / 任务列表 / 删除线 / 自动链接
 *   remark-math → `$...$` 与 `$$...$$` 变成 math 节点
 *   rehype-sanitize → 白名单过滤（**在 katex 之前**，否则会削掉 KaTeX 的 class/style）
 *   rehype-katex → 真正的公式排版
 *
 * 另外 react-markdown 默认**不启用 rehype-raw**，所以 Markdown 里的原始 HTML
 * 根本不会变成 DOM；链接/图片的协议白名单在 `a` / `img` 组件里做第二道闸。
 */

/** 放行 KaTeX 输出需要的 class / style / MathML+SVG 标签。 */
const SCHEMA = {
  ...defaultSchema,
  tagNames: [
    ...(defaultSchema.tagNames ?? []),
    'math',
    'semantics',
    'annotation',
    'annotation-xml',
    'mrow',
    'mi',
    'mo',
    'mn',
    'msup',
    'msub',
    'msubsup',
    'mfrac',
    'msqrt',
    'mroot',
    'mtext',
    'mspace',
    'mtable',
    'mtr',
    'mtd',
    'munder',
    'mover',
    'munderover',
    'mpadded',
    'mphantom',
    'svg',
    'path',
    'g',
    'line',
    'rect',
    'foreignObject',
  ],
  attributes: {
    ...defaultSchema.attributes,
    '*': [
      ...(defaultSchema.attributes?.['*'] ?? []),
      'className',
      'style',
      'id',
      'role',
      'ariaHidden',
      'ariaLabel',
      'encoding',
    ],
    code: [...(defaultSchema.attributes?.code ?? []), ['data-harness-renderer', 'true'], ['data-harness-closed', 'true', 'false']],
    math: ['xmlns', 'display'],
    annotation: ['encoding'],
    svg: ['xmlns', 'viewBox', 'width', 'height', 'preserveAspectRatio', 'style', 'className'],
    path: ['d', 'fill', 'stroke', 'strokeWidth'],
    line: ['x1', 'x2', 'y1', 'y2', 'stroke', 'strokeWidth'],
    g: ['fill', 'stroke', 'transform'],
  },
  // KaTeX 会输出 <annotation encoding="application/x-tex"> 里的原始 TeX。
  protocols: {
    ...defaultSchema.protocols,
    // sanitize 后还会经 urlTransform 与 resolveLink：只允许带目录分隔符
    // 的盘符路径，c:foo 等自定义协议不会变成可执行链接。
    href: ['http', 'https', 'mailto', 'tel', ...'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')],
    src: ['http', 'https', 'data', 'blob', ...'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')],
  },
}

const HEADINGS: Record<number, string> = {
  1: 'mt-7 mb-3 text-xl font-semibold tracking-tight',
  2: 'mt-6 mb-2.5 border-b border-line pb-1.5 text-lg font-semibold tracking-tight',
  3: 'mt-5 mb-2 text-base font-semibold',
  4: 'mt-4 mb-1.5 text-sm font-semibold',
  5: 'mt-4 mb-1.5 text-sm font-semibold text-muted',
  6: 'mt-4 mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted',
}

function heading(level: 1 | 2 | 3 | 4 | 5 | 6) {
  const Tag = ('h' + level) as 'h1'
  return function Heading({ children, id }: { children?: ReactNode; id?: string }) {
    return (
      <Tag id={id} tabIndex={id ? -1 : undefined} className={cn('scroll-mt-24 text-ink', HEADINGS[level])}>{children}</Tag>
    )
  }
}

const LINK_ICONS = {
  external: SquareArrowOutUpRight,
  mail: Mail,
  anchor: Link2,
  file: FileText,
  image: FileText,
  video: FileText,
  audio: FileText,
  pdf: FileText,
  fileDefault: FileText,
  unsafe: AlertCircle,
} as const

function LinkIcon({ kind }: { kind: ResolvedLink['kind'] }) {
  const Cmp =
    (LINK_ICONS as Record<string, typeof Link2>)[kind] ?? FileText
  return <Cmp className="inline h-3 w-3 shrink-0 opacity-60" />
}

/* ============================ 图片 ============================ */

function MarkdownImage({
  src,
  alt,
  title,
}: {
  src?: string
  alt?: string
  title?: string
}) {
  useLocale()

  const documentSource = useContext(DocumentSource)

  if (!src) return null
  const resolved = resolveLink(src, true, documentSource?.links)

  if (resolved.kind === 'unsafe' || !resolved.href) {
    return (
      <span className="text-danger inline-flex items-center gap-1 text-xs">
        <AlertCircle className="h-3.5 w-3.5" />{t("ui.components.markdown.Markdown.087844e8ebb3")}</span>
    )
  }

  return <ResolvedMarkdownImage key={resolved.href} resolved={resolved} src={src} alt={alt} title={title} document={Boolean(documentSource)} />
}

function ResolvedMarkdownImage({ resolved, src, alt, title, document: inDocument }: {
  resolved: ResolvedLink
  src: string
  alt?: string
  title?: string
  document: boolean
}) {
  useLocale()
  const openLightbox = useAppStore((s) => s.openLightbox)
  const [failed, setFailed] = useState(false)

  if (failed) {
    return (
      <span className="border-line bg-raised text-muted my-3 inline-flex items-center gap-2 rounded-lg border border-dashed px-3 py-2 text-xs">
        <AlertCircle className="h-3.5 w-3.5 shrink-0 text-warn" />{t("ui.components.markdown.Markdown.572993f5820a")}<code className="text-2xs">{resolved.workspacePath ?? src}</code>
      </span>
    )
  }

  return (
    <button
      type="button"
      onClick={() => openLightbox(resolved.href, alt ?? resolved.workspacePath)}
      className={cn('group/img my-3 block max-w-full', inDocument ? 'w-full' : 'w-fit')}
      title={title ?? alt ?? t("ui.components.markdown.Markdown.fea4c16d2986")}
    >
      <img
        src={resolved.href}
        alt={alt ?? ''}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
        className={cn('max-h-image-preview cursor-zoom-in rounded-xl border border-line object-contain shadow-card transition-transform duration-200 ease-swift group-hover/img:scale-preview', inDocument && 'mx-auto')}
      />
      {alt ? (
        <span className="text-fade mt-1 block text-center text-2xs text-faint">{alt}</span>
      ) : null}
    </button>
  )
}

/* ============================ 链接 ============================ */

function MarkdownLink({
  href,
  children,
  title,
}: {
  href?: string
  children?: ReactNode
  title?: string
}) {
  useLocale()

  const reveal = useAppStore((s) => s.revealInFiles)
  const documentSource = useContext(DocumentSource)
  const workspaceLinks = useContext(WorkspaceLinks)
  if (!workspaceLinks && (!href || !/^https?:\/\//i.test(href))) return <>{children}</>
  if (!href) return <>{children}</>

  const resolved = resolveLink(href, false, documentSource?.links)

  if (resolved.kind === 'unsafe') {
    // 危险协议：只显示文本，绝不生成可点击的 a。
    return (
      <Tooltip content={t("ui.components.markdown.Markdown.6c1fc40b443c")}>
        <span className="text-danger cursor-help border-b border-dotted border-danger/50">
          {children}
        </span>
      </Tooltip>
    )
  }

  // 链接按地址来源路由；媒体扩展名不能覆盖文件预览或浏览器打开行为。
  const external = resolved.external && isSafeExternal(resolved.href)
  const attrs = { href: resolved.href, ...(external ? externalAttrs() : {}) }
  const lineHint = resolved.lineRange
    ? t("ui.components.markdown.Markdown.8c9dc085b154", {p0: (resolved.lineRange.start), p1: (resolved.lineRange.end ? '-' + resolved.lineRange.end : '')})
    : ''

  return (
    <a
      {...attrs}
      download={resolved.download || undefined}
      onClick={resolved.workspacePath && !resolved.download ? (event) => { event.preventDefault(); reveal(resolved.workspacePath!, resolved.lineRange, resolved.workspaceRoot === undefined ? undefined : { root: resolved.workspaceRoot }) }
        : documentSource && resolved.kind === 'anchor' && href.startsWith('#') ? (event) => {
          event.preventDefault()
          if (resolved.lineRange) { reveal(documentSource.file.path, resolved.lineRange, { root: documentSource.file.root }); return }
          let fragment = href.slice(1)
          try { fragment = decodeURIComponent(fragment) } catch { /* Keep malformed references visible. */ }
          const heading = document.getElementById('user-content-' + documentSource.prefix + headingSlug(fragment))
          const reader = heading?.closest('[data-markdown-reader]')
          if (heading && reader) {
            reader.scrollTop += heading.getBoundingClientRect().top - reader.getBoundingClientRect().top - 16
            heading.focus({ preventScroll: true })
          } else useAppStore.getState().toast({ title: t("ui.components.markdown.Markdown.c9a8b662d5db"), description: fragment, tone: 'info' })
        } : undefined}
      title={title ?? t('ui.markdown.linkTitle', { action: resolved.download ? t("ui.components.markdown.Markdown.3926d1bb98be") : resolved.workspacePath ? t("ui.components.markdown.Markdown.ffde79eba293") : external ? t("ui.components.markdown.Markdown.c744c961f0ab") : t("ui.components.markdown.Markdown.a2639495cb4d"), destination: resolved.workspacePath ?? resolved.href, line: lineHint })}
      className="group/link inline-flex max-w-full items-baseline gap-1"
    >
      <span className="truncate">{children}</span>
      <LinkIcon kind={external ? 'external' : resolved.kind} />
    </a>
  )
}

/* ============================ 根组件 ============================ */

const makeComponents = () => ({
  h1: heading(1),
  h2: heading(2),
  h3: heading(3),
  h4: heading(4),
  h5: heading(5),
  h6: heading(6),
  a: MarkdownLink,
  img: MarkdownImage,
  pre: ({ children }: { children?: ReactNode }) => <>{children}</>,
  code: ({ node, className, children }: { node?: { properties?: Record<string, unknown> }; className?: string; children?: ReactNode }) => {
    const streaming = useContext(Streaming)
    const match = /language-([\w+-]+)/.exec(className ?? '')
    const text = String(children ?? '').replace(/\n$/, '')
    const closed = node?.properties?.['data-harness-closed'] === 'true'
    if (node?.properties?.['data-harness-renderer'] === 'true') {
      return <RendererFrame code={text} closed={closed} streaming={streaming} className="my-3" />
    }
    if (match?.[1]?.toLowerCase() === 'html') {
      return <CodeBlock code={text} language="html" previewReady={closed} className="my-3" />
    }
    const isBlock = text.includes('\n') || Boolean(match)
    if (!isBlock) {
      return <code className={className}>{children}</code>
    }
    // 缩进围栏（列表项 / 引用里的图）切段时不会被提出来，只能在这里兜底。
    // 已闭合的图不再跟随后续正文保持流式状态。
    return (
      <LiveChart
        code={text}
        lang={match?.[1]}
        closed={closed || !streaming}
        className="my-3"
      />
    )
  },
  input: (props: React.InputHTMLAttributes<HTMLInputElement>) => {
    const { checked, disabled } = props
    return (
      <span
        className={cn(
          'border-line-strong mr-1.5 inline-flex h-4 w-4 translate-y-0.5 items-center justify-center rounded border align-middle',
          checked && 'border-brand bg-brand',
          disabled && 'opacity-60',
        )}
        aria-checked={Boolean(checked)}
        role="checkbox"
      >
        {checked ? (
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden>
            <path
              d="M1.8 5.2 3.9 7.3 8.2 2.9"
              stroke="var(--c-brand-ink)"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        ) : null}
      </span>
    )
  },
  table: ({ children, ...rest }: { children?: ReactNode }) => (
    <div className="my-4 overflow-x-auto rounded-lg border border-line">
      <table className="w-full border-collapse text-sm" {...rest}>
        {children}
      </table>
    </div>
  ),
  hr: () => <hr className="border-line my-7" />,
})

export interface MarkdownProps {
  children: string
  className?: string
  /** 用户消息里的内容更紧凑一些。 */
  dense?: boolean
  /**
   * 流式输出中。开启后会做两件只在「半个句子」下才需要的事：
   *   1. 补齐未闭合的 `$` / `$$`，让 KaTeX 不会整段退回纯文本；
   *   2. 图表段走 LiveChart 的实时渲染分支。
   */
  streaming?: boolean
  /** File reader context: relative links and images resolve from this document, not the chat. */
  fileSource?: MarkdownFileSource
  /** 非工作区内容可关闭自动文件跳转，保留外部链接。 */
  workspaceLinks?: boolean
}

// 组件类型保持稳定，语言切换和流式收尾不能卸载已运行的嵌套 iframe。
const COMPONENTS = makeComponents()

const REMARK = [remarkGfm, remarkMath, remarkOutputReferences, remarkRenderer] as never

type Components = ReturnType<typeof makeComponents>

/**
 * 单个 Markdown 段。
 *
 * 单独 memo 是这一层最重要的优化：流式期每来一个 token，`segmentStream`
 * 只会让**最后一段**的文本变化，前面定稿的段落 props 完全相同，
 * 于是 remark/rehype 一次都不会重跑。长回答里这是数量级的差别 ——
 * 整段重解析意味着每次都要重新处理早已稳定的表格、公式和代码块。
 */
const MarkdownSegment = memo(function MarkdownSegment({
  text,
  streaming,
  components,
}: {
  text: string
  streaming: boolean
  components: Components
}) {
  const locale = useLocale()

  const body = streaming ? stabilizeMarkdown(text) : text
  const [math, setMath] = useState(currentMathRenderer)
  const [mathFailed, setMathFailed] = useState(false)
  const needsMath = body.includes('$')
  useEffect(() => {
    if (!needsMath || math) return
    let current = true
    const timer = setTimeout(() => { if (current) setMathFailed(true) }, 10000)
    void loadMathRenderer().then(value => { if (current) {setMath(() => value); setMathFailed(false)} }, () => { if (current) setMathFailed(true) }).finally(() => clearTimeout(timer))
    return () => { current = false; clearTimeout(timer) }
  }, [needsMath, math])
  const retryMath = () => {
    setMathFailed(false)
    void loadMathRenderer().then(value => setMath(() => value), () => setMathFailed(true))
  }
  const rehype = useMemo(() => [
    [rehypeSanitize, SCHEMA],
    ...(math ? [[math, { throwOnError: false, strict: false }]] : []),
  ] as never, [math])
  const documentSource = useContext(DocumentSource)
  const workspaceLinks = useContext(WorkspaceLinks)
  const remark = useMemo(() => !workspaceLinks ? [remarkGfm, remarkMath, remarkRenderer] as never : documentSource ? [remarkGfm, remarkMath, remarkOutputReferences, remarkRenderer, [remarkDocumentHeadings, { prefix: documentSource.prefix }]] as never : REMARK, [documentSource, locale, workspaceLinks])
  if (!body.trim()) return null
  return (
    <>
    {needsMath && mathFailed && !math ? <p role="alert" className="text-xs text-warn">{t('ui.deferred.math.failed')} <button type="button" className="inline-flex items-center gap-1 underline" onClick={retryMath}><RotateCw aria-hidden className="h-3 w-3" />{t('ui.deferred.reload')}</button></p> : null}
    <Streaming.Provider value={streaming}><ReactMarkdown
      urlTransform={(url) => /^[A-Za-z]:(?:[\\/]|%5c|%2f)/i.test(url) ? url.replace(/%5c/gi, '/') : defaultUrlTransform(url)}
      remarkPlugins={remark}
      rehypePlugins={rehype}
      components={components as never}
      skipHtml
    >
      {body}
    </ReactMarkdown></Streaming.Provider>
    </>
  )
})

export const Markdown = memo(function Markdown({
  children,
  className,
  dense,
  streaming = false,
  fileSource,
  workspaceLinks = true,
}: MarkdownProps) {
  const locale = useLocale()

  const documentId = useId()
  const documentSource = useMemo(() => {
    if (!fileSource) return undefined
    const relative = relativizeToRoot(fileSource.path, fileSource.root).replace(/\\/g, '/')
    const separator = relative.lastIndexOf('/')
    return { file: fileSource, links: { root: fileSource.root, directory: separator < 0 ? '' : relative.slice(0, separator) }, prefix: 'file-' + documentId.replace(/:/g, '') + '-' }
  }, [fileSource?.path, fileSource?.root, documentId, locale])
  // 原始 <img>/<video>/<audio> 先翻译成 Markdown 链接，
  // 再交给文件链接 / 图片灯箱（管线本身仍然 skipHtml）。
  const raw = useMemo(
    // Trailing whitespace delimits streamed SVG coordinates. Preserve it so
    // the path renderer can commit the last received complete segment.
    () => normalizeMediaTags(children ?? ''),
    [children, locale],
  )
  const segments = useMemo<StreamSegment[]>(() => fileSource ? [{ kind: 'markdown', text: raw, key: 'document', last: true }] : segmentStream(raw), [raw, Boolean(fileSource), locale])
  const components = COMPONENTS

  if (!raw) return null

  return (
    <WorkspaceLinks.Provider value={workspaceLinks}><DocumentSource.Provider value={documentSource}>
    <div className={cn('md', dense && 'text-compact leading-reading', className)}>
      {segments.map((segment: StreamSegment) =>
        segment.kind === 'renderer' ? (
          <RendererFrame
            key={segment.key}
            code={segment.code}
            closed={segment.closed}
            streaming={streaming}
            className="my-3"
          />
        ) : segment.kind === 'chart' && segment.lang === 'html' ? (
          <CodeBlock key={segment.key} code={segment.code} language="html" previewReady={segment.closed} className="my-3" />
        ) : segment.kind === 'chart' ? (
          <LiveChart
            key={segment.key}
            code={segment.code}
            lang={segment.lang}
            closed={segment.closed || !streaming}
            className="my-3"
          />
        ) : (
          <MarkdownSegment
            key={segment.key}
            text={segment.text}
            streaming={streaming && segment.last}
            components={components}
          />
        ),
      )}
    </div>
    </DocumentSource.Provider></WorkspaceLinks.Provider>
  )
})
