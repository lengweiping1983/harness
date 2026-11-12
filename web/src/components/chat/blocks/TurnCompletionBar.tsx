import { t, useLocale } from '@/i18n'
import {MessageTimestamp} from './MessageTimestamp'
import { useEffect, useRef, useState, type ButtonHTMLAttributes } from 'react'
import { Anchor, Check, Copy, GitFork, Loader2, ThumbsDown, ThumbsUp } from 'lucide-react'
import { api, unwrap } from '@/lib/api'
import { ApiError, isUnconfirmedWrite, runtimeRequest } from '@/lib/http'
import type { ResourceText } from '@/types'
import { ResourceError, failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { cn, copyText } from '@/lib/utils'
import { readReply, type CompletionRow, type TurnCompletion } from '@/lib/turnCompletion'
import { useAppStore } from '@/store/app'
import { HookStatisticsDialog } from './HookStatisticsDialog'
import { TurnResults } from './TurnResults'
import { Tooltip } from '@/components/ui/Tooltip'

function ActionButton({ title, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  useLocale()

  return <Tooltip content={title}><button {...props} type="button" /></Tooltip>
}

type CompletionProps = {row: CompletionRow; sessionId: string | null}

export function TurnCompletionBar(props: CompletionProps) {
  const {sessionId, row} = props
  return <CompletionActions key={JSON.stringify([sessionId, row.turn, row.endSeq, row.finalSeq])} {...props} />
}

function CompletionActions({ row, sessionId }: CompletionProps) {
  useLocale()

  const [data, setData] = useState<TurnCompletion | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [actionError, setActionError] = useState<{ message: ResourceText; retry: () => void } | null>(null)
  const [retry, setRetry] = useState(0)
  const [forkError, setForkError] = useState<ResourceText>('')
  const [showHooks, setShowHooks] = useState(false)
  const [offset, setOffset] = useState(0)
  const [busy, setBusy] = useState('')
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(true)
  const lock = useRef(false)
  const lifetime = useRef<AbortController | null>(null)
  const path = `/sessions/${encodeURIComponent(sessionId ?? '')}/turns/${row.turn}`
  useEffect(() => {
    const controller = new AbortController(); lifetime.current = controller
    setActionError(null)
    return () => controller.abort()
  }, [sessionId, row.endSeq])
  useEffect(() => {
    if (!sessionId) return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    void runtimeRequest<TurnCompletion>(path + '/completion?offset=' + offset, 'GET', undefined, { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return
      if (!value || value.turn !== row.turn || value.end_seq !== row.endSeq || value.final_response_seq !== row.finalSeq) throw new ResourceError({key: "ui.components.chat.blocks.TurnCompletionBar.866862db3616"})
      setData(value)
    }).catch(e => { if (!controller.signal.aborted) setError(failureText(e)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [path, sessionId, row.endSeq, row.finalSeq, row.turn, offset, retry])

  const perform = async (name: string, work: (signal: AbortSignal) => Promise<void>) => {
    if (lock.current || !sessionId || !lifetime.current || name === 'fork' && Boolean(forkError)) return
    lock.current = true; setBusy(name); setActionError(null)
    const signal = lifetime.current.signal
    try { await work(signal) } catch (e) {
      if (name === 'fork' && (!(e instanceof ApiError) || isUnconfirmedWrite(e.failure))) {
        if (!signal.aborted) {
          setForkError({template: {key: 'ui.turnCompletion.forkUnconfirmed', params: {reason: failureText(e)}}})
        }
        // 分叉可能已创建，回查目录但不重放；关闭原操作栏也不取消已接受写入的收尾。
        await useAppStore.getState().refreshSessions()
      } else if (!signal.aborted) setActionError({ message: failureText(e), retry: () => void perform(name, work) })
    }
    finally { lock.current = false; if (!signal.aborted) setBusy('') }
  }
  const rate = (rating: 'positive' | 'negative') => void perform('feedback', async signal => {
    if (!data || loading) return
    const value = await runtimeRequest<{ end_seq: number; feedback: TurnCompletion['feedback'] }>(path + '/feedback', 'POST', { end_seq: row.endSeq, rating: data.feedback === rating ? null : rating }, { signal })
    if (signal.aborted) return
    if (!value || value.end_seq !== row.endSeq) throw new ResourceError({key: 'ui.components.chat.blocks.TurnCompletionBar.866862db3616'})
    if (![null, 'positive', 'negative'].includes(value.feedback)) throw new ResourceError({key: 'ui.http.invalidResponse'})
    setData(current => current && ({ ...current, feedback: value.feedback }))
  })
  const buttonClass = 'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-faint hover:bg-raised hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-40'
  return <section aria-label={t("ui.components.chat.blocks.TurnCompletionBar.60df34e06168", {p0: (row.turn)})} className="min-w-0 pb-2" data-turn-completion={row.turn} aria-busy={loading}>
    {data && sessionId ? <TurnResults data={data} sessionId={sessionId} loading={loading} readFailed={Boolean(error)} onRefresh={() => setRetry(value => value + 1)} /> : null}
    <div className="flex min-w-0 flex-wrap items-center gap-0.5">
      <ActionButton aria-label={t("ui.components.chat.blocks.TurnCompletionBar.d784b8b766ea")} title={data && data.response_seq === null ? t("ui.components.chat.blocks.TurnCompletionBar.noReply") : t("ui.components.chat.blocks.TurnCompletionBar.d784b8b766ea")} disabled={Boolean(busy) || loading || !data || data.response_seq === null} className={buttonClass}
        onClick={() => void perform('copy', async signal => {
          if (!data || data.response_seq === null) return
          const seq = data.response_seq
          const text = await readReply(sessionId!, seq, start => unwrap(api.readConversationMessage(sessionId!, seq, start, signal)))
          if (signal.aborted) return
          if (!await copyText(text)) throw new ResourceError({key: "ui.components.chat.blocks.TurnCompletionBar.2e9a64f29c7e"})
          if (!signal.aborted) { setCopied(true); useAppStore.getState().toast({ title: {key: "ui.components.chat.blocks.TurnCompletionBar.8c039c6a861e"}, tone: 'success' }) }
        })}>{busy === 'copy' ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : copied ? <Check aria-hidden className="h-3.5 w-3.5" /> : <Copy aria-hidden className="h-3.5 w-3.5" />}</ActionButton>
      <ActionButton aria-label={t("ui.components.chat.blocks.TurnCompletionBar.28030e690447")} title={t("ui.components.chat.blocks.TurnCompletionBar.7a4f732cde30")} aria-pressed={data?.feedback === 'positive'} disabled={Boolean(busy) || loading || !data} className={cn(buttonClass, data?.feedback === 'positive' && 'bg-brand/10 text-brand')} onClick={() => rate('positive')}><ThumbsUp aria-hidden className="h-3.5 w-3.5" /></ActionButton>
      <ActionButton aria-label={t("ui.components.chat.blocks.TurnCompletionBar.3a31207261f6")} title={t("ui.components.chat.blocks.TurnCompletionBar.310255d2f498")} aria-pressed={data?.feedback === 'negative'} disabled={Boolean(busy) || loading || !data} className={cn(buttonClass, data?.feedback === 'negative' && 'bg-brand/10 text-brand')} onClick={() => rate('negative')}><ThumbsDown aria-hidden className="h-3.5 w-3.5" /></ActionButton>
      <ActionButton aria-label={t("ui.components.chat.blocks.TurnCompletionBar.e13f6ee9b3ab")} title={t("ui.components.chat.blocks.TurnCompletionBar.7d68ad1245d6")} disabled={Boolean(busy) || Boolean(forkError)} className={buttonClass}
        onClick={() => void perform('fork', async signal => {
          const owner = sessionId
          const result = await runtimeRequest<{ session: { id: string } }>(`/sessions/${encodeURIComponent(owner!)}/fork`, 'POST', { through_seq: row.endSeq })
          if (typeof result?.session?.id !== 'string' || !result.session.id.trim() || result.session.id === owner) throw new ResourceError({key: 'ui.http.invalidResponse'})
          await useAppStore.getState().refreshSessions()
          if (!signal.aborted && useAppStore.getState().activeSessionId === owner) useAppStore.getState().selectSession(result.session.id)
        })}>{busy === 'fork' ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : <GitFork aria-hidden className="h-3.5 w-3.5" />}</ActionButton>
      <ActionButton aria-label={t("ui.components.chat.blocks.TurnCompletionBar.909ee3161e8a")} title={t("ui.components.chat.blocks.TurnCompletionBar.909ee3161e8a")} aria-expanded={showHooks} className={cn(buttonClass, showHooks && 'bg-raised text-ink')} onClick={() => setShowHooks(value => !value)}><Anchor aria-hidden className="h-3.5 w-3.5" /></ActionButton>
      <MessageTimestamp at={row.at} kind="completed" className="ml-2 text-xs text-faint" />
    </div>
    {loading && sessionId ? <p role="status" className="mt-1 flex items-center gap-1.5 text-xs text-muted"><Loader2 aria-hidden className="h-3 w-3 animate-spin motion-reduce:animate-none" />{t('ui.turnCompletion.loading')}</p> : null}
    {actionError ? <div role="alert" className="mt-1 flex flex-wrap gap-2 text-xs text-danger"><span>{noticeText(actionError.message)}</span><button type="button" className="underline" disabled={Boolean(busy)} onClick={actionError.retry}>{t("ui.components.chat.blocks.TurnCompletionBar.fa660d296961")}</button></div> : null}
    {forkError ? <div role="alert" className="mt-1 text-xs text-danger">{noticeText(forkError)}</div> : null}
    {error && !showHooks ? <div role="alert" className="mt-1 flex flex-wrap gap-2 text-xs text-danger"><span>{noticeText(error)}</span><button type="button" className="underline" onClick={() => setRetry(n => n + 1)}>{t("ui.components.chat.blocks.TurnCompletionBar.df7392cc96be")}</button></div> : null}
    {showHooks ? <HookStatisticsDialog data={data} sessionId={sessionId} loading={loading} error={error}
      onRetry={() => setRetry(n => n + 1)} onPage={setOffset} onClose={() => setShowHooks(false)} /> : null}
  </section>
}
