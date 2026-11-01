import {useDialogFocus} from '@/hooks/useDialogFocus'
import {usePageVisible} from '@/hooks/usePageVisible'
import {useAppStore} from '@/store/app'
import { t, useLocale } from '@/i18n'
import { useEffect, useId, useRef, useState } from 'react'
import { Activity, AlertTriangle, Maximize2, Minimize2, RotateCw } from 'lucide-react'
import { cn, debounce } from '@/lib/utils'
import { Button, IconButton, Spinner, Tooltip } from '@/components/ui/primitives'

/**
 * Mermaid 图表。
 *
 * mermaid 本体 ~2MB+，绝不能进首屏包：这里用 `import()` 在第一次真正遇到
 * 图表时才拉；初始化与渲染串行执行，避免多个图表的主题配置互相覆盖。
 * 渲染失败不能白屏——降级成带错误提示的源码块，让用户至少能读到图的内容。
 *
 * 流式（`live`）下语法天然不完整，所以：
 *   - 停止输入一段时间后才尝试渲染，避免把半句话喂给解析器；
 *   - 解析失败不弹错误条，只显示"正在绘制"骨架，等围栏闭合再定性。
 *
 * 等待时长按图的大小自适应：mermaid 一次 render 要跑布局测量，成本随节点数
 * 明显上升。固定 320ms 对小图有点拖，对上百行的时序图则是每个 token 都在
 * 主线程上砸一次布局 —— 那正是流式输出最卡的时候。
 */

/** 流式重绘的等待窗口：小图 260ms 起，节点越多等得越久，封顶 1.3s。 */
function settleDelay(chart: string): number {
  const size = chart.length
  if (size <= 200) return 260
  return Math.min(1300, 260 + Math.round(size / 6))
}

/** mermaid 的 d.ts 把 default 导成了整个模块命名空间，直接取类型不可靠，这里只声明用到的两个方法。 */
interface MermaidApi {
  initialize(config: Record<string, unknown>): void
  render(id: string, text: string): Promise<{ svg: string }>
}

let loader: Promise<MermaidApi> | null = null
let initializedTheme: boolean | null = null
let renderQueue: Promise<unknown> = Promise.resolve()

function loadMermaid() {
  if (!loader) {
    loader = import('mermaid').then((m) => {
      const bag = m as unknown as { default?: MermaidApi }
      return bag.default ?? (m as unknown as MermaidApi)
    }).catch(error => {loader = null; throw error})
  }
  return loader
}

async function getMermaid(dark: boolean) {
  const mermaid = await loadMermaid()
  if (initializedTheme !== dark) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: dark ? 'dark' : 'default',
      fontFamily:
        'ui-sans-serif, system-ui, "PingFang SC", "Microsoft YaHei", sans-serif',
      flowchart: { curve: 'basis', htmlLabels: true, useMaxWidth: true },
      sequence: { useMaxWidth: true },
      gantt: { useMaxWidth: true },
      themeVariables: dark
        ? {
            primaryColor: 'rgba(129,140,248,.16)',
            primaryTextColor: '#e6e9f0',
            primaryBorderColor: 'rgba(129,140,248,.55)',
            lineColor: 'rgba(154,164,184,.75)',
            secondaryColor: 'rgba(245,183,76,.14)',
            tertiaryColor: 'rgba(34,197,94,.12)',
            fontSize: '13px',
          }
        : { fontSize: '13px' },
    })
    initializedTheme = dark
  }
  return mermaid
}

function renderChart(dark: boolean, id: string, chart: string, current: () => boolean) {
  // 下载可与其他图的渲染并行，主题初始化和 DOM 布局仍严格串行。
  const download = loadMermaid()
  void download.catch(() => undefined)
  const result = renderQueue.then(async () => {
    await download
    if (!current()) return null
    const mermaid = await getMermaid(dark)
    return mermaid.render(id, chart)
  })
  renderQueue = result.catch(() => undefined)
  return result
}

export function Mermaid({
  chart,
  live = false,
  className,
}: {
  chart: string
  live?: boolean
  className?: string
}) {
  useLocale()
  const pageVisible = usePageVisible()
  const settingsOpen = useAppStore(state => state.settingsOpen)
  const active = pageVisible && !settingsOpen

  const [svg, setSvg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [waiting, setWaiting] = useState(live)
  const [expired, setExpired] = useState(false)
  const watchdog = useRef<ReturnType<typeof setTimeout>>()
  const startWaiting = () => {
    clearTimeout(watchdog.current)
    setExpired(false)
    watchdog.current = setTimeout(() => setExpired(true), 10000)
  }
  const [zoom, setZoom] = useState(false)
  const figureRef = useRef<HTMLElement | null>(null)
  useDialogFocus(figureRef, zoom, () => setZoom(false))
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  useEffect(() => {
    if (!active) return
    const observer = new MutationObserver(() => setDark(document.documentElement.classList.contains('dark')))
    setDark(document.documentElement.classList.contains('dark'))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [active])
  const id = useId().replace(/[:]/g, '')
  const renderId = 'mmd-' + id
  const attempt = useRef(0)

  useEffect(() => {
    if (!active) return
    let cancelled = false
    void loadMermaid().catch(() => { /* 渲染入口统一报告错误。 */ })
    const run = async () => {
      attempt.current += 1
      const myAttempt = attempt.current
      setLoading(true)
      startWaiting()
      try {
        // 必须每次换 id：mermaid 会把 <style>/临时 DOM 挂在 id 上。
        const result = await renderChart(dark, renderId + '-' + myAttempt, chart, () => !cancelled && myAttempt === attempt.current)
        if (!result || cancelled || myAttempt !== attempt.current) return
        setSvg(result.svg)
        setError(null)
        setExpired(false)
      } catch (err) {
        if (cancelled || myAttempt !== attempt.current) return
        setSvg(null)
        // 流式期语法不完整是常态，不当作错误，只等下一轮。
        if (live) return
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled && myAttempt === attempt.current) {
          clearTimeout(watchdog.current)
          setLoading(false)
          setWaiting(false)
        }
      }
    }

    if (live) {
      setWaiting(true)
      // 停止输入后再解析：既省 CPU，也避免半截箭头把图炸成语法错误。
      const schedule = debounce(() => void run(), settleDelay(chart))
      schedule()
      return () => {
        cancelled = true
        attempt.current++
        clearTimeout(watchdog.current)
        schedule.cancel?.()
      }
    }

    setWaiting(false)
    void run()
    return () => {
      cancelled = true
      attempt.current++
      clearTimeout(watchdog.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart, live, dark, active])

  if (error || expired) {
    return (
      <figure className={cn('code-block', className)}>
        <figcaption className="flex h-9 items-center gap-2 border-b border-line bg-warn/8 px-3">
          <AlertTriangle className="h-3.5 w-3.5 text-warn" />
          <span className="font-mono text-2xs font-medium uppercase tracking-wide text-warn">
            mermaid
          </span>
          <span className="text-2xs text-faint">{t("ui.components.markdown.Mermaid.7d511b302f18")}</span>
          <Button
            size="xs"
            variant="ghost"
            className="ml-auto"
            icon={<RotateCw className="h-3 w-3" />}
            onClick={() => {
              attempt.current += 1
              setError(null)
              setSvg(null)
              setLoading(true)
              startWaiting()
              const myAttempt = attempt.current
              void renderChart(dark, renderId + '-retry-' + myAttempt, chart, () => myAttempt === attempt.current).then(
                (r) => {
                  if (!r || myAttempt !== attempt.current) return
                  setSvg(r.svg)
                  setLoading(false)
                  setExpired(false)
                  clearTimeout(watchdog.current)
                },
                (e: unknown) => {
                  if (myAttempt !== attempt.current) return
                  setError(e instanceof Error ? e.message : String(e))
                  setLoading(false)
                  clearTimeout(watchdog.current)
                },
              )
            }}
          >{t("ui.components.markdown.Mermaid.b8784c8dd563")}</Button>
        </figcaption>
        <pre className="overflow-x-auto px-4 py-3 font-mono text-compact leading-code text-muted">
          <code>{chart}</code>
        </pre>
      </figure>
    )
  }

  const drawing = waiting || (loading && !svg)

  return (
    <figure
      ref={figureRef}
      role={zoom ? 'dialog' : undefined}
      aria-modal={zoom ? true : undefined}
      aria-label={zoom ? 'Mermaid' : undefined}
      tabIndex={zoom ? -1 : undefined}
      className={cn(
        'group relative overflow-hidden rounded-xl border border-line bg-raised/40',
        zoom && 'fixed inset-4 z-expanded my-0 bg-surface shadow-pop',
        className,
      )}
      onDoubleClick={() => setZoom((v) => !v)}
    >
      <figcaption className="flex h-8 items-center gap-2 border-b border-line px-3">
        <span className="font-mono text-2xs font-medium uppercase tracking-wide text-faint">
          mermaid
        </span>
        {live ? (
          <span className="inline-flex items-center gap-1 rounded-md bg-brand/[0.12] px-1.5 py-0.5 text-2xs font-medium text-brand">
            <Activity className="h-3 w-3" />
            {drawing ? t("ui.components.markdown.Mermaid.2e6c82266860") : t("ui.components.markdown.Mermaid.c69ec2ffb0db")}
          </span>
        ) : null}
        <div className="ml-auto flex items-center gap-0.5">
          {loading && svg ? <Spinner /> : null}
          <Tooltip content={zoom ? t("ui.components.markdown.Mermaid.ddde08946298") : t("ui.components.markdown.Mermaid.80f8fbcfa011")}>
            <IconButton
              label={zoom ? t("ui.components.markdown.Mermaid.ddde08946298") : t("ui.components.markdown.Mermaid.80f8fbcfa011")}
              size="xs"
              onClick={() => setZoom((v) => !v)}
            >
              {zoom ? (
                <Minimize2 className="h-3.5 w-3.5" />
              ) : (
                <Maximize2 className="h-3.5 w-3.5" />
              )}
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
        {drawing ? (
          <div role="status" aria-label={t("ui.components.markdown.Mermaid.2e6c82266860")} className="shimmer-bar h-24 w-full max-w-md rounded-lg" />
        ) : svg ? (
          <div
            className="mermaid-host w-full [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full"
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        ) : (
          <div role="status" aria-label={t("ui.components.markdown.Mermaid.2e6c82266860")} className="shimmer-bar h-24 w-full max-w-md rounded-lg" />
        )}
      </div>
      {zoom ? (
        <button
          type="button"
          aria-label={t("ui.components.markdown.Mermaid.e6a269e847a1")}
          className="absolute inset-0 -z-10"
          onClick={() => setZoom(false)}
        />
      ) : null}
    </figure>
  )
}
