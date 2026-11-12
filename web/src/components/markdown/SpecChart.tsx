import {memo, useEffect, useMemo, useState} from 'react'
import {t, useLocale} from '@/i18n'
import {usePageVisible} from '@/hooks/usePageVisible'
import {useAppStore} from '@/store/app'
import {chartDocument, parseChartSpec, type ChartKind} from '@/lib/chartSpec'
import {loadChartRuntime} from '@/lib/chartRuntime'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {WidgetCard, WidgetLoading} from '@/components/chat/blocks/WidgetCard'
import type {WidgetPayload} from '@/lib/widgetPayload'
import {CodeBlock} from './CodeBlock'

/** 完整配置才加载库；并发共享下载，迟到结果归属原配置，失败可显式重试。 */
export const SpecChart = memo(function SpecChart({source, kind, closed = true, className, payload, active: enabled = true}: {
  source: string; kind: ChartKind; closed?: boolean; className?: string; payload?: WidgetPayload; active?: boolean
}) {
  useLocale()
  const visible = usePageVisible()
  const settingsOpen = useAppStore(state => state.settingsOpen)
  const active = enabled && visible && !settingsOpen
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState<{kind: ChartKind; runtime: string} | null>(null)
  const [failed, setFailed] = useState<{kind: ChartKind; attempt: number} | null>(null)
  const parsed = useMemo(() => {
    if (!closed) return null
    try {return {spec: parseChartSpec(source, kind)}} catch (error) {return {error: failureText(error)}}
  }, [source, kind, closed])
  const valid = Boolean(parsed?.spec)
  useEffect(() => {
    if (!active || !valid || loaded?.kind === kind) return
    let current = true
    const timer = setTimeout(() => {if (current) setFailed({kind, attempt})}, 10000)
    void loadChartRuntime(kind).then(runtime => {
      if (current) {setLoaded({kind, runtime});setFailed(null)}
    }, () => {if (current) setFailed({kind, attempt})}).finally(() => clearTimeout(timer))
    return () => {current = false;clearTimeout(timer)}
  }, [kind, active, valid, attempt, loaded?.kind])
  const document = useMemo(() => parsed?.spec && loaded?.kind === kind ? chartDocument(kind, parsed.spec, loaded.runtime) : null, [parsed, loaded, kind])
  const widget = useMemo<WidgetPayload>(() => payload ?? {title: kind, kind, code: source, chars: source.length, loadingMessages: []}, [payload, kind, source])
  const failure = parsed && 'error' in parsed ? parsed.error : null
  if (failure || failed?.kind === kind && failed.attempt === attempt) return <div className={className}>
    <p role="alert" className="text-xs text-warn">{failure ? noticeText(failure) : t('ui.chart.loadFailed')}{!failure ? <button type="button" className="ml-2 underline" onClick={() => setAttempt(value => value + 1)}>{t('ui.chart.retry')}</button> : null}</p>
    <CodeBlock code={source} language="json" />
  </div>
  return <div className={className}>{document ? <WidgetCard payload={widget} previewCode={document} active={active} /> : <WidgetLoading preview={{title: kind, loadingMessages: [t(closed ? 'ui.chart.loading' : 'ui.chart.streaming')]}} />}</div>
})
