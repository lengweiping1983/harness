import { LearnHarnessButton } from '@/components/ui/LearnHarnessButton'
import { t, useLocale } from '@/i18n'
import { memo, useId, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { ToolActivityIcon } from '@/components/ui/ToolActivityIcon'
import type { ToolBlockState } from '@/types'
import { cn, formatDuration, isProbablyJson, stringify } from '@/lib/utils'
import { todoFailure } from '@/lib/todoFailure'
import { toolArgsFailure, toolArgsNote, fileWriteRejected } from '@/lib/toolArgsFailure'
import { toolActivity } from '@/lib/toolActivity'
import { commandFailed, commandResult } from '@/lib/commandResults'
import { toolImages, imageOutputSummary } from '@/lib/toolImages'
import { widgetPayload, widgetPreview } from '@/lib/widgetPayload'
import { WidgetCard, WidgetLoading } from './WidgetCard'
import { ImageGallery } from './ImageGallery'
import { Badge, CopyButton } from '@/components/ui/primitives'
import { JsonView } from '@/components/ui/JsonView'
import { JsonSource } from '@/components/ui/JsonSource'
import { Markdown, FileChip } from '@/components/markdown/Markdown'
import { DiffView } from './DiffView'
import { QuestionCard } from './QuestionCard'
import { savedQuestionAnswers } from '@/lib/questionAnswers'
import { HookActivityList } from './HookActivity'
import { useAppStore } from '@/store/app'
import { backgroundJobId } from '@/lib/jobHistory'
import { useActivityDisclosure } from '@/hooks/useActivityDisclosure'
import { authoredPlugin } from '@/lib/pluginReview'
import { usePluginReview } from '@/store/pluginReview'
import { ToolResultReader, type ToolResultScope } from './ToolResultReader'

export interface ToolBlockProps { block: ToolBlockState; defaultOpen?: boolean; sessionId?: string | null; resultScope?: ToolResultScope }

export const ToolBlock = memo(function ToolBlock({ block, defaultOpen, sessionId = null, resultScope }: ToolBlockProps) {
  const locale = useLocale()
  const detailId = useId()
  const activeSessionId = useAppStore(state => state.activeSessionId)
  const plugin = authoredPlugin(block)

  const { open, toggle } = useActivityDisclosure(block.status === 'running', Boolean(defaultOpen), false,
    JSON.stringify([sessionId ?? activeSessionId, block.id, block.callId]))
  const progressView = useRef<HTMLPreElement>(null)
  const followOutput = useRef(true)
  const activity = useMemo(() => toolActivity(block), [block.name, block.input, block.status, block.display, locale])
  const todoIssue = todoFailure(block)
  const command = commandResult(block)
  const images = useMemo(() => toolImages(block), [block.name, block.output, block.display, block.input, locale])
  const widget = useMemo(() => widgetPayload(block), [block.name, block.status, block.display])
  const widgetPending = useMemo(() => widgetPreview(block), [block.name, block.status, block.input])
  const isWidget = block.name === 'show_widget'
  const argsRejected = toolArgsFailure(block)
  const fileRejected = fileWriteRejected(block)
  const notExecuted = argsRejected || Boolean(block.display && typeof block.display === 'object' && 'not_executed' in block.display && block.display.not_executed)
  const deferred = Boolean(block.display && typeof block.display === 'object' && 'steering_deferred' in block.display && block.display.steering_deferred)
  const skipped = notExecuted || deferred
  const failed = block.status === 'error' || commandFailed(block)
  const running = block.status === 'running'
  const files = activity.files
  const output = block.outputKey ? t(block.outputKey) : block.output ?? block.display
  const savedQuestion = useMemo(() => savedQuestionAnswers(block), [block.name, block.status, block.input, block.output, block.display, locale])
  const outputText = useMemo(() => (typeof output === 'string' ? images.length ? imageOutputSummary(output) : output : ''), [output, images.length, locale])
  const outputIsJson = output !== undefined && !outputText && typeof output === 'object'
  const outputJsonText = outputText.trim().length > 0 && isProbablyJson(outputText)
  const outputMarkdown = block.status !== 'error' && activity.kind !== 'command' && outputText.trim().length > 0 && !outputJsonText
  const progress = block.progress
  const jobId = backgroundJobId(block.display)
  const progressText = progress ? progress.stdout + (progress.stderr ? `${progress.stdout && !progress.stdout.endsWith('\n') ? '\n' : ''}[stderr]\n${progress.stderr}` : '') : ''
  const detailStatus = command.label ?? (running ? t("ui.components.chat.blocks.ToolBlock.5026a63b58cd") : notExecuted ? t("ui.components.chat.blocks.ToolBlock.231fe2204265") : deferred ? t("ui.components.chat.blocks.ToolBlock.09b08165d88e") : (block.display as { interrupted?: boolean } | undefined)?.interrupted ? t("ui.components.chat.blocks.ToolBlock.bb48a89c508b") : block.status === 'error' ? t("ui.components.chat.blocks.ToolBlock.28384d7afd2e") : (block.display as { background?: boolean } | undefined)?.background ? t("ui.components.chat.blocks.ToolBlock.9606b203ed7b") : t("ui.components.chat.blocks.ToolBlock.c0b3fbff51cc"))
  const detailTone = running ? 'brand' : failed && !skipped ? 'danger' : skipped || (block.display as { interrupted?: boolean; background?: boolean } | undefined)?.interrupted || (block.display as { background?: boolean } | undefined)?.background ? 'warn' : 'neutral'
  useLayoutEffect(() => {
    const view = progressView.current
    if (view && !document.hidden && view.getClientRects().length && followOutput.current) view.scrollTop = view.scrollHeight
  }, [progress?.seq, open])
  return (
    <div className="group/tool min-w-0" data-tool-call-id={block.callId}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls={detailId}
        className={cn('flex w-full min-w-0 items-center gap-2 rounded-md px-1 text-left text-compact leading-5 focus-visible:outline-brand', isWidget ? 'min-h-8 py-1' : 'conversation-activity min-h-6 gap-1.5 py-0.5', open && 'font-medium text-ink', failed && !skipped ? 'text-danger' : 'text-muted hover:text-ink')}
      >
        <ToolActivityIcon kind={activity.kind} name={block.name} running={running} skipped={skipped} failed={failed} />
        <span className="min-w-0 max-w-full truncate" title={activity.label}>{images.length && block.status !== 'running' ? t(block.status === 'error' ? 'ui.toolBlock.imagesPartial' : 'ui.toolBlock.imagesViewed', {count: images.length}) : activity.label}</span>
        {activity.detail ? <span className="min-w-0 truncate text-faint" title={activity.detail}>{activity.detail}</span> : null}
        <ChevronRight aria-hidden className={cn('h-3.5 w-3.5 shrink-0 text-faint transition-transform duration-200', open && 'rotate-90')} />
      </button>

      {todoIssue ? <div role="note" className="ml-5 mb-2 space-y-1 rounded-md border border-line bg-raised p-3 text-xs leading-reading">
        <p className="font-medium text-ink">{todoIssue.reason}</p>
        <p className="text-muted">{t('ui.todoFailure.unchanged')}</p>
        <p className="text-muted">{todoIssue.recovery}</p>
      </div> : null}
      {argsRejected || fileRejected ? <p role="note" className="ml-5 mb-2 rounded-md border border-line bg-raised p-3 text-xs leading-reading text-muted">{fileRejected ? t('ui.fileWrite.readFirst') : toolArgsNote(block)}</p> : null}
      {progress?.encodingErrors?.map((issue, index) => <p key={index} role="alert" className="ml-5 text-xs text-warn">{t('ui.encoding.failure', {...issue})}</p>)}
      {block.hooks?.length ? <HookActivityList rows={block.hooks} sessionId={sessionId} /> : null}
      {plugin && sessionId ? <button type="button" disabled={activeSessionId !== sessionId}
        className="ml-5 mb-1 max-w-full text-left text-xs text-brand [overflow-wrap:anywhere] hover:underline focus-visible:underline disabled:opacity-50"
        onClick={() => {
          if (useAppStore.getState().activeSessionId !== sessionId) return
          usePluginReview.getState().open(sessionId, plugin.path)
          useAppStore.getState().setSettingsOpen(true, 'plugins')
        }}>{t('ui.authoring.reviewPlugin', {name: plugin.name})}</button> : null}
      {jobId && sessionId ? <button type="button" className="ml-5 mb-1 text-xs text-brand hover:underline focus-visible:underline"
        onClick={() => useAppStore.getState().revealJob(sessionId, jobId)}>{t("ui.components.chat.blocks.ToolBlock.19ff770e458e")}</button> : null}

      {block.name === 'browser' && sessionId ? <button type="button" className="ml-5 mb-1 text-xs text-brand hover:underline focus-visible:underline" onClick={() => useAppStore.getState().setInspectorTab('browser')}>{t("ui.components.chat.blocks.ToolBlock.4d689adda874")}</button> : null}
      {images.length ? <ImageGallery images={images} /> : null}
      {open ? (
        <div id={detailId} role="group" aria-label={block.name} data-tool-detail="" className="ml-5 mb-2 mt-1 min-w-0 overflow-hidden rounded-xl border border-line bg-surface/50">
          <header className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line bg-raised/70 px-3 py-2">
            <code className="min-w-0 break-all text-sm font-semibold text-ink">{block.name}</code>
            <Badge tone={detailTone}>{detailStatus}</Badge>
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs tabular-nums text-muted">
              {typeof block.durationMs === 'number' ? <span>{t('ui.toolBlock.duration', {duration: formatDuration(block.durationMs)})}</span> : null}
              {command.exitCode !== null ? <span>{t('ui.toolBlock.exitCode', {code: command.exitCode})}</span> : null}
            </span>
          </header>
          <div className="space-y-3 p-3">
          {command.note ? <p className="text-xs leading-relaxed text-warn">{command.note}</p> : null}
          {running && progressText ? <div>
            <pre ref={progressView} aria-label={t("ui.components.chat.blocks.ToolBlock.8f3ea71ba079")} tabIndex={0}
              onScroll={(event) => { const view = event.currentTarget; followOutput.current = view.scrollHeight - view.scrollTop - view.clientHeight < 24 }}
              className="max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-raised/35 p-3 font-mono text-xs leading-5 text-muted">{progressText}</pre>
            {progress && progress.stdoutDropped + progress.stderrDropped > 0 ? <p className="py-1 text-2xs text-faint">{t("ui.components.chat.blocks.ToolBlock.96c9ed2242ad")}</p> : null}
          </div> : null}
          {!running && progressText ? <details className="text-xs text-muted"><summary className="cursor-pointer">{t("ui.components.chat.blocks.ToolBlock.c84acf9d0015")}</summary>
            <pre tabIndex={0} className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md bg-raised p-3 font-mono text-xs leading-5">{progressText}</pre>
            {progress && progress.stdoutDropped + progress.stderrDropped > 0 ? <p className="py-1 text-2xs text-faint">{t("ui.components.chat.blocks.ToolBlock.bed49546e98b")}</p> : null}
          </details> : null}
          {files.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-2xs text-faint">{t("ui.components.chat.blocks.ToolBlock.bb77e03d732f")}</span>
              {files.map((file) => (
                <FileChip key={file} path={file} />
              ))}
            </div>
          ) : null}

          <Section label={t("ui.components.chat.blocks.ToolBlock.2087c777c06f")} right={<CopyButton text={() => stringify(block.input, 100000)} contentIdentity={block.input} />}>
            {block.input === undefined ? (
              <Empty>{t("ui.components.chat.blocks.ToolBlock.c336c3a5f5ba")}</Empty>
            ) : (
              <JsonView value={block.input} className="rounded-none border-0 bg-transparent p-0" />
            )}
          </Section>

          {todoIssue || argsRejected || fileRejected ? <details className="text-xs text-muted"><summary className="cursor-pointer focus-visible:outline-brand">{t('ui.todoFailure.diagnostics')}</summary>
            {typeof (output ?? block.display) === 'object' || isProbablyJson(output ?? block.display)
              ? <JsonSource text={stringify(output ?? block.display, 200000)} className="mt-2 max-h-60" />
              : <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{stringify(output ?? block.display, 200000)}</pre>}
            {block.contentRef && (resultScope || sessionId) ? <ToolResultReader key={JSON.stringify([resultScope?.sessionId ?? sessionId, resultScope?.agentId, block.contentRef])}
              reference={block.contentRef} scope={resultScope ?? { sessionId: sessionId! }} /> : null}
          </details> : block.contentRef ? (
            <Section label={t('ui.toolResult.original')}>
              {block.status !== 'error' ? <DiffView output={undefined} display={block.display} path={files[0] ?? null} /> : null}
              {resultScope || sessionId ? <ToolResultReader key={JSON.stringify([resultScope?.sessionId ?? sessionId, resultScope?.agentId, block.contentRef])}
                reference={block.contentRef} scope={resultScope ?? { sessionId: sessionId! }} /> : <Empty>{t('ui.toolResult.unavailable')}</Empty>}
            </Section>
          ) : output !== undefined && output !== null && output !== '' ? (
            <Section
              label={skipped ? t("ui.components.chat.blocks.ToolBlock.d57835f46fb4") : block.status === 'error' ? t("ui.components.chat.blocks.ToolBlock.c7a2084a61c2") : t("ui.components.chat.blocks.ToolBlock.fb04addb4c26")}
              right={<CopyButton text={() => stringify(output, 200000)} contentIdentity={output} />}
            >
              {/* 文件改动不走 JSON 树：edit 返回的 unified diff 和
                  文件修改优先使用后端返回的 diff 视图。
                  extractFileDiff 拿不到可对比的内容时返回 null，
                  这里自然回退到下面的通用渲染分支。 */}
              {block.status !== 'error' ? (
                <DiffView output={output} display={block.display} path={files[0] ?? null} />
              ) : null}
              {running ? (
                <div className="shimmer-bar h-12 w-full rounded-lg" />
              ) : savedQuestion ? (
                <div aria-label={t("ui.components.chat.blocks.ToolBlock.d1166c319527")}><QuestionCard block={savedQuestion} sessionId={null} onAnswer={() => {}} /></div>
              ) : outputIsJson ? (
                <JsonView value={output} className="rounded-none border-0 bg-transparent p-0" />
              ) : outputJsonText ? (
                <JsonSource text={outputText} className="max-h-tool-output rounded-none border-0 bg-transparent p-0" />
              ) : outputMarkdown ? (
                <Markdown dense className="text-compact">
                  {outputText}
                </Markdown>
              ) : (
                <pre className="text-muted max-h-tool-output overflow-auto whitespace-pre-wrap break-words font-mono text-xs">
                  {outputText}
                </pre>
              )}
            </Section>
          ) : running ? (
            <div className="shimmer-bar h-8 w-full rounded-lg" />
          ) : null}
          </div>
          <footer className="flex justify-end border-t border-line bg-raised/30 px-2 py-1">
            <LearnHarnessButton topic="tool" />
          </footer>
        </div>
      ) : null}
      {widget || widgetPending ? <div className="mt-2 min-w-0">
        {widget ? <WidgetCard key={`${sessionId ?? activeSessionId}:${block.callId}:${block.id}`} payload={widget} /> : <WidgetLoading preview={widgetPending} />}
      </div> : null}
    </div>
  )
})

function Section({
  label,
  right,
  children,
}: {
  label: string
  right?: ReactNode
  children: ReactNode
}) {
  useLocale()

  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-line bg-surface/60">
      <header className="flex min-h-8 items-center justify-between gap-2 border-b border-line bg-raised/50 px-2.5 py-1">
        <h5 className="text-xs font-semibold text-ink">{label}</h5>
        <div className="opacity-0 transition-opacity group-hover/tool:opacity-100 focus-within:opacity-100">
          {right}
        </div>
      </header>
      <div className="min-w-0 p-2.5">{children}</div>
    </section>
  )
}

function Empty({ children }: { children: ReactNode }) {
  useLocale()

  return <span className="text-fade text-2xs text-faint">{children}</span>
}
