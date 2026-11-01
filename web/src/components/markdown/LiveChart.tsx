import {useDialogFocus} from '@/hooks/useDialogFocus'
import {usePageVisible} from '@/hooks/usePageVisible'
import {useAppStore} from '@/store/app'
import { t, useLocale } from '@/i18n'
import { memo, useDeferredValue, useId, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  Check,
  Download,
  Maximize2,
  Minimize2,
  PencilRuler,
  RotateCcw,
} from 'lucide-react'
import { cn, download, formatNumber } from '@/lib/utils'
import {
  parseProgressiveSvg,
  scopeSvg,
  patchSvg,
  strokePrep,
  type ParsedSvg,
} from '@/lib/progressiveSvg'
import { createFrameThrottle, type ThrottleSink } from '@/lib/frameThrottle'
import { normalizeLang } from '@/lib/streaming'
import { CopyButton, IconButton, Spinner, Tooltip } from '@/components/ui/primitives'
import { CodeBlock } from './CodeBlock'
import { Mermaid } from './Mermaid'

/**
 * 图表统一入口（"边画边展示"的核心）。
 *
 * 模型画图有两条主力路径，两条都要支持流式增量：
 *   - ```mermaid  —— 声明式图（流程/时序/甘特/饼图/xychart…）。写一半语法不成立，
 *                    就先给"正在绘制"骨架，语法一旦合法立刻出图。
 *   - ```svg      —— 逐字符画。走 `progressiveSvg` 的补齐管线，模型画到哪就显示到哪，
 *                    接收到的线段直接填入画布，完成时保留已有图形。
 *
 * 其余（vega / vega-lite / echarts / plotly）不引额外运行时依赖，
 * 解析成功就给结构摘要卡片 + 源码，明确告诉用户"这是一份图表规范"。
 * 引 1MB+ 的图表库换一个"能看但不能画"，不划算。
 */

export interface LiveChartProps {
  code: string
  lang?: string
  /** 围栏是否已闭合。false = 还在流式输出中。 */
  closed?: boolean
  className?: string
}

const SPEC_LANGS = new Set(['vega', 'vega-lite', 'echarts', 'chart', 'plotly'])

export const LiveChart = memo(function LiveChart({
  code,
  lang,
  closed = true,
  className,
}: LiveChartProps) {
  useLocale()

  const value = normalizeLang(lang)

  if (value === 'mermaid') return <Mermaid chart={code} live={!closed} className={className} />
  if (value === 'svg') return <ProgressiveSvgView source={code} live={!closed} className={className} />
  if (SPEC_LANGS.has(value)) return <SpecChart source={code} kind={value} live={!closed} className={className} />
  // 没标语言的围栏里如果塞了 SVG（模型很爱这么干），也照样画出来。
  if (!value && /<svg\b/i.test(code)) {
    return <ProgressiveSvgView source={code} live={!closed} className={className} />
  }
  return <CodeBlock code={code} language={lang} className={className} previewReady={closed} />
})

/* ============================ SVG 渐进渲染 ============================ */

interface ProgressiveSvgViewProps {
  source: string
  live: boolean
  className?: string
}

function ProgressiveSvgView({ source, live, className }: ProgressiveSvgViewProps) {
  useLocale()
  const scope = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const pageVisible = usePageVisible()
  const settingsOpen = useAppStore(state => state.settingsOpen)
  const active = pageVisible && !settingsOpen

  // 首次上屏立刻解析一次，避免骨架多闪一帧；之后由下面的节流器接管。
  const [parsed, setParsed] = useState<ParsedSvg | null>(() => active ? scopeSvg(parseProgressiveSvg(source), scope) : null)
  const [zoom, setZoom] = useState(false)
  const figureRef = useRef<HTMLElement | null>(null)
  useDialogFocus(figureRef, zoom, () => setZoom(false))
  const hostRef = useRef<HTMLDivElement | null>(null)
  const sinkRef = useRef<ThrottleSink<string> | null>(null)

  // 帧预算节流器（只在流式期存在）。
  //
  // 关键点：**被节流的是字符串，不是解析结果**。若在 push 之前就
  // scopeSvg(parseProgressiveSvg(source), scope)，重算次数依旧等于 token 数，节流形同虚设；
  // 必须把解析搬进 commit 回调，节流窗口内的几十个 token 才真的只算一次。
  useEffect(() => {
    if (!live || !active) return
    const sink = createFrameThrottle<string>((value) => {
      setParsed(scopeSvg(parseProgressiveSvg(value), scope))
    })
    sinkRef.current = sink
    // 前沿提交：画布马上出现，不等第一个节流窗口。
    sink.push(source)
    return () => {
      sink.cancel()
      sinkRef.current = null
    }
  }, [live, active])

  // 每次 token 推进只是「把最新源码丢进节流器」，一次字符串赋值。
  useEffect(() => {
    if (!active) return
    if (!live) {
      // 围栏闭合：终值必须立刻、精确地画出来，不走节流。
      // （此时上面那个 effect 的 cleanup 已经先跑过，节流器已销毁，不会再有
      //   迟到的回调把中间态覆盖回来。）
      setParsed(scopeSvg(parseProgressiveSvg(source), scope))
      return
    }
    sinkRef.current?.push(source)
  }, [live, source, active])

  const markup = parsed?.markup ?? ''

  // Incremental drawing reflects received geometry, including growing paths.
  //
  // 这里**不再**用 dangerouslySetInnerHTML 整块重解析：模型每个 token 都会
  // 触发一次，那样长图会被反复从零重建，已画好的部分每帧都在闪，动画也永远
  // 重播。patchSvg 只改动真正变化的那一个节点，于是「画到哪、动到哪」。
  useEffect(() => {
    const host = hostRef.current
    if (!active || !host || !parsed?.root) return
    patchSvg(host, parsed.root)
  }, [parsed, active])

  const renderable = Boolean(markup)

  return (
    <figure
      ref={figureRef}
      role={zoom ? 'dialog' : undefined}
      aria-modal={zoom ? true : undefined}
      aria-label={zoom ? 'SVG' : undefined}
      tabIndex={zoom ? -1 : undefined}
      className={cn(
        'group relative overflow-hidden rounded-xl border border-line bg-raised/40',
        zoom && 'fixed inset-4 z-expanded my-0 bg-surface shadow-pop',
        className,
      )}
    >
      <figcaption className="flex h-8 items-center gap-2 border-b border-line px-3">
        <span className="font-mono text-2xs font-medium uppercase tracking-wide text-faint">
          svg
        </span>
        {live ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-brand/[0.12] px-1.5 py-0.5 text-2xs font-medium text-brand">
            <Activity className="h-3 w-3" />{t("ui.components.markdown.LiveChart.eebee11ffc8b")}</span>
        ) : parsed?.repaired ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-warn/[0.12] px-1.5 py-0.5 text-2xs font-medium text-warn">
            <AlertTriangle className="h-3 w-3" />{t("ui.components.markdown.LiveChart.1f06a566db54")}</span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-md bg-ok/[0.12] px-1.5 py-0.5 text-2xs font-medium text-ok">
            <Check className="h-3 w-3" />{t("ui.components.markdown.LiveChart.088b27d0ed00")}</span>
        )}
        <span className="ml-auto flex items-center gap-1 text-2xs text-faint">
          {parsed?.width && parsed?.height ? `${parsed.width}×${parsed.height}` : null}
          {parsed?.drawable ? t("ui.components.markdown.LiveChart.e6ace198c351", {p0: (parsed.drawable)}) : null}
        </span>
        <div className="flex items-center gap-0.5">
          {renderable ? <Spinner className={live ? '' : 'hidden'} /> : null}
          {renderable && !live ? (
            <Tooltip content={t("ui.components.markdown.LiveChart.ad9a4e83ff36")}>
              <IconButton
                label={t("ui.components.markdown.LiveChart.ad9a4e83ff36")}
                size="xs"
                onClick={() => {
                  const host = hostRef.current
                  if (host) strokePrep(host, true)
                }}
              >
                <RotateCcw className="h-3.5 w-3.5" />
              </IconButton>
            </Tooltip>
          ) : null}
          {/* 复制给原始源码（模型写的原样），导出给清洗+补齐后的合法 SVG：
              后者才是能双击打开、能在 Illustrator 里继续编辑的东西。 */}
          <CopyButton text={source} />
          <Tooltip content={t("ui.components.markdown.LiveChart.5a61f98bac78")}>
            <IconButton
              label={t("ui.components.markdown.LiveChart.5a61f98bac78")}
              size="xs"
              disabled={!renderable}
              onClick={() => download('chart.svg', markup, 'image/svg+xml')}
            >
              <Download className="h-3.5 w-3.5" />
            </IconButton>
          </Tooltip>
          <Tooltip content={zoom ? t("ui.components.markdown.LiveChart.ddde08946298") : t("ui.components.markdown.LiveChart.80f8fbcfa011")}>
            <IconButton label={zoom ? t("ui.components.markdown.LiveChart.ddde08946298") : t("ui.components.markdown.LiveChart.80f8fbcfa011")} size="xs" onClick={() => setZoom((v) => !v)}>
              {zoom ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
            </IconButton>
          </Tooltip>
        </div>
      </figcaption>
      <div
        className={cn(
          'grid-noise grid min-h-20 place-items-center overflow-auto p-4',
          zoom && 'h-zoom',
        )}
      >
        {renderable ? (
          // 空宿主：patchSvg 负责往里填。绝不能让 React 管 children，
          // 否则下一次渲染会把这棵增量长出来的 SVG 冲掉。
          <div
            ref={hostRef}
            className="chart-host w-full [&>svg]:mx-auto [&>svg]:h-auto [&>svg]:max-w-full"
          />
        ) : (
          <div className="shimmer-bar h-24 w-full max-w-md rounded-lg" />
        )}
      </div>
      {zoom ? (
        <button
          type="button"
          aria-label={t("ui.components.markdown.LiveChart.e6a269e847a1")}
          className="absolute inset-0 -z-10"
          onClick={() => setZoom(false)}
        />
      ) : null}
    </figure>
  )
}

/* ============================ 图表规范（vega / echarts） ============================ */

interface SpecSummary {
  mark?: string
  encoding?: string[]
  dataRows?: number
  series?: number
  charts?: string[]
  json: boolean
}

function summarizeSpec(source: string): SpecSummary {
  const out: SpecSummary = { encoding: [], charts: [], json: false }
  let parsed: unknown
  try {
    parsed = JSON.parse(source)
    out.json = true
  } catch {
    // 半截 JSON 很正常，流式期就靠这个判断"还没写完"。
    return out
  }
  if (Array.isArray(parsed)) {
    // echarts option 数组
    for (const item of parsed as Array<Record<string, unknown>>) {
      const series = item?.series
      if (Array.isArray(series)) {
        out.series = (out.series ?? 0) + series.length
        for (const s of series as Array<Record<string, unknown>>) {
          const type = String(s?.type ?? '')
          if (type && !out.charts!.includes(type)) out.charts!.push(type)
        }
      }
    }
    return out
  }
  if (!parsed || typeof parsed !== 'object') return out
  const root = parsed as Record<string, unknown>
  const mark = root.mark
  if (typeof mark === 'string') out.mark = mark
  else if (mark && typeof mark === 'object') out.mark = String((mark as Record<string, unknown>).type ?? '')
  const encoding = root?.encoding
  if (encoding && typeof encoding === 'object') out.encoding = Object.keys(encoding as object)
  const data = root?.data
  if (data && typeof data === 'object') {
    const values = (data as Record<string, unknown>).values
    if (Array.isArray(values)) out.dataRows = values.length
    else if (typeof (data as Record<string, unknown>).url === 'string') out.dataRows = -1
  }
  const series = root?.series
  if (Array.isArray(series)) {
    out.series = series.length
    for (const s of series as Array<Record<string, unknown>>) {
      const type = String(s?.type ?? '')
      if (type && !out.charts!.includes(type)) out.charts!.push(type)
    }
  }
  return out
}

function SpecChart({
  source,
  kind,
  live,
  className,
}: {
  source: string
  kind: string
  live: boolean
  className?: string
}) {
  const locale = useLocale()

  const deferred = useDeferredValue(source)
  const summary = useMemo(() => summarizeSpec(deferred), [deferred, locale])

  return (
    <div className={cn('space-y-2', className)}>
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-line bg-raised/40 px-2.5 py-2">
        <PencilRuler className="h-3.5 w-3.5 text-brand" />
        <span className="font-mono text-2xs font-medium uppercase tracking-wide text-faint">
          {kind}
        </span>
        {live ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-brand/[0.12] px-1.5 py-0.5 text-2xs font-medium text-brand">
            <Activity className="h-3 w-3" />{t("ui.components.markdown.LiveChart.a12c13825bf3")}</span>
        ) : summary.json ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-ok/[0.12] px-1.5 py-0.5 text-2xs font-medium text-ok">
            <Check className="h-3 w-3" />{t("ui.components.markdown.LiveChart.ea768086965d")}</span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-md bg-warn/[0.12] px-1.5 py-0.5 text-2xs font-medium text-warn">
            <AlertTriangle className="h-3 w-3" />{t("ui.components.markdown.LiveChart.ccecaa77c8b3")}</span>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-1.5 text-2xs text-faint">
          {summary.mark ? <Chip label={t('ui.chart.summary.mark')} value={summary.mark} /> : null}
          {summary.encoding?.length ? (
            <Chip label={t('ui.chart.summary.encoding')} value={t("ui.components.markdown.LiveChart.5edd92b6a548", {p0: (summary.encoding.length)})} />
          ) : null}
          {summary.dataRows === -1 ? (
            <Chip label={t('ui.chart.summary.data')} value={t('ui.chart.summary.url')} />
          ) : summary.dataRows ? (
            <Chip label={t('ui.chart.summary.data')} value={t("ui.components.markdown.LiveChart.d4a79ad79ced", {p0: (formatNumber(summary.dataRows))})} />
          ) : null}
          {summary.series ? <Chip label={t('ui.chart.summary.series')} value={formatNumber(summary.series)} /> : null}
          {summary.charts?.length ? (
            <Chip label={t('ui.chart.summary.type')} value={summary.charts.join(' / ')} />
          ) : null}
        </div>
      </div>
      <CodeBlock code={source} language={kind} />
    </div>
  )
}

function Chip({ label, value }: { label: string; value: string }) {
  useLocale()

  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-1.5 py-0.5">
      <span className="text-faint">{label}</span>
      <span className="font-medium text-muted">{value}</span>
    </span>
  )
}
