import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import type { Ref } from 'react'
import { Check, ChevronDown, Cpu, Gauge, Search, Sparkles, X } from 'lucide-react'
import type { ModelInfo, ResourceText } from '@/types'
import { cn, formatNumber } from '@/lib/utils'
import { filterModels, groupModelsByProvider, modelDisplayName } from '@/lib/modelMenu'
import { sessionModelOf } from '@/lib/workspaces'
import { modelSaveKey, modelWriteUnconfirmed, useActiveSession, useAppStore } from '@/store/app'
import { Kbd, Tooltip } from '@/components/ui/primitives'
import { reasoningSelection } from '@/lib/reasoningSelection'
import { useConnectionRoutes } from '@/lib/connectionRoutes'

/**
 * 模型选择：composer 上的 "model seat" + `/model` 唤起的同一份菜单。
 *
 * 按钮和 `/model` 共用同一目录与菜单，保持排序和选择行为一致。
 *
 * 行为要点：
 *   - 按 provider 分组；
 *   - 模型多于 4 个才显示搜索框（3 个模型时搜索框是纯噪音）；
 *   - ↑↓/Enter/Tab/Esc 全键盘可用；
 *   - 思考强度是**子行**，不是另一个菜单 —— 它和模型是同一个决定；
 *   - 选择保存后由后续运行采用；已开始的运行保留请求配置快照。
 */

/** 搜索框出现的阈值：少于等于这个数就直接列出来。 */
const SEARCH_THRESHOLD = 4

export function ModelSeat({
  className,
  buttonRef,
}: {
  className?: string
  buttonRef?: Ref<HTMLButtonElement>
}) {
  useLocale()

  const models = useAppStore((s) => s.models)
  const defaultModel = useAppStore((s) => s.defaultModel)
  const session = useActiveSession()
  const effort = useAppStore(s => reasoningSelection(session, s.defaultReasoningEffort).value)
  const reasoningUnconfirmed = useAppStore(s => {
    const inherited = reasoningSelection(session, s.defaultReasoningEffort).inherited
    const own = s.activeSessionId ? s.reasoningSaves[s.activeSessionId] : undefined
    const save = own?.pending || own?.error || !inherited ? own : s.defaultReasoningSave
    return Boolean(save?.pending || save?.unconfirmed)
  })
  const routing = useConnectionRoutes()
  const pending = useAppStore(s => Boolean(s.modelSaves[modelSaveKey(s.activeSessionId)]
    || !session?.model && s.modelSaves[modelSaveKey(null)]))
  const defaultTarget = useAppStore(s => !session?.model && s.modelSaves[modelSaveKey(null)] ? s.defaultModelSave?.target : null)
  // 开合状态放在 store：``/model`` 命令也要能打开**同一个**菜单，
  // 各开各的状态会导致"输入 /model 后按钮还是收起的样子"。
  const open = useAppStore((s) => s.modelMenuOpen)
  const setOpen = useAppStore((s) => s.setModelMenuOpen)

  const routed = Boolean(routing?.models.length)
  const current = routed ? routing?.current?.model ?? '' : defaultTarget ?? sessionModelOf(session, defaultModel)
  const keyIndex = routing?.models.find((m) => m.id === routing.current?.model_id)?.keys.findIndex((k) => k.id === routing.current?.key_id)
  const routeLabel = routed && current ? t("ui.components.chat.ModelPicker.601c04f996bd", {p0: (current), p1: ((keyIndex ?? 0) + 1), p2: (routing?.models.filter((m) => m.enabled).length ?? 0)}) : null
  const modelName = (modelDisplayName(current, models, t('ui.models.unavailableName')) || t("ui.components.chat.ModelPicker.c98e118e0a43")).split('/').at(-1) || t("ui.components.chat.ModelPicker.c98e118e0a43")
  const characters = Array.from(modelName)
  const compactName = characters.length > 10 ? characters.slice(0, 9).join('') + '…' : modelName
  const label = routed ? t('ui.models.routingCandidate', {model: routeLabel ?? t('ui.models.candidateUnknown')}) : t(pending ? 'ui.models.selectionSaving' : 'ui.components.chat.ModelPicker.8b625429e269', {p0: modelDisplayName(current, models, t('ui.models.unavailableName')) || t('ui.components.chat.ModelPicker.a44f9f9fcaca')})
  const currentInfo = models.find(model => model.id === current)
  const reasoningLabel = !routed && (currentInfo?.supports_reasoning || effort != null)
    ? t(reasoningUnconfirmed ? 'ui.models.reasoningUnconfirmed' : effort != null && currentInfo && !currentInfo.reasoning_efforts?.includes(effort)
      ? 'ui.models.reasoningUnsupported' : 'ui.models.reasoningSelection', {effort: effort ?? t('ui.components.chat.ModelPicker.9a36b187cea4')}) : null
  const accessibleLabel = reasoningLabel ? t('ui.models.selectionWithReasoning', {model: label, reasoning: reasoningLabel}) : label

  return (
    <div className={cn('relative max-w-full', className)}>
      <Tooltip content={accessibleLabel}>
        <button
          type="button"
          ref={buttonRef}
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label={accessibleLabel}
          className={cn(
            'pointer-events-auto inline-flex h-7 max-w-60 items-center gap-1.5 rounded-lg border px-2 text-2xs font-medium transition-all duration-150',
            open
              ? 'border-brand/40 bg-brand/[0.12] text-brand'
              : 'border-line bg-raised/60 text-muted hover:text-ink',
          )}
        >
          <Sparkles className="h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 truncate">{compactName}</span>
          {reasoningLabel ? <span className="min-w-0 truncate border-l border-line pl-1.5">{reasoningLabel}</span> : null}
          {!routed && pending ? <span className="shrink-0 text-faint">{t('ui.models.selectionPending')}</span> : null}
          <ChevronDown className={cn('h-3 w-3 shrink-0 transition-transform', open && 'rotate-180')} />
        </button>
      </Tooltip>

    </div>
  )
}

interface ModelMenuProps {
  models: ModelInfo[]
  current: string
  effort?: string | null
  onClose: () => void
  className?: string
  /** 菜单标题，`/model` 从输入框里唤起时用得上。 */
  title?: string
  menuVisible?: boolean
}

export function ModelMenu(props: ModelMenuProps) {
  const sessionId = useAppStore(state => state.activeSessionId)
  return <ModelMenuInstance key={JSON.stringify(sessionId)} {...props} />
}

function ModelMenuInstance({ models, current, effort, onClose, className, title = t("ui.components.chat.ModelPicker.1afed6a81a25"), menuVisible = true }: ModelMenuProps) {
  const locale = useLocale()

  const routing = useConnectionRoutes()
  const setReasoningEffort = useAppStore((s) => s.setReasoningEffort)
  const reasoningSave = useAppStore((s) => {
    const inherited = reasoningSelection(s.sessions.find(session => session.id === s.activeSessionId), s.defaultReasoningEffort).inherited
    return s.activeSessionId && (!inherited || s.reasoningSaves[s.activeSessionId]?.pending || s.reasoningSaves[s.activeSessionId]?.error)
      ? s.reasoningSaves[s.activeSessionId] : s.defaultReasoningSave
  })
  const defaultReasoningSave = useAppStore((s) => s.defaultReasoningSave)
  const sessionId = useAppStore((s) => s.activeSessionId)
  const session = useActiveSession()
  const creatingSession = useAppStore((s) => s.sessionCreating)
  const inheritedEffort = useAppStore((s) => reasoningSelection(s.sessions.find((session) => session.id === s.activeSessionId), s.defaultReasoningEffort).inherited)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [localPending, setPending] = useState(false)
  const sharedPending = useAppStore((state) => Boolean(state.modelSaves[modelSaveKey(state.activeSessionId)]))
  const pending = localPending || sharedPending
  const pendingSave = useRef(false)
  const mounted = useRef(true)
  const [modelFeedback, setModelFeedback] = useState<{ id: string; message: ResourceText; unconfirmed: boolean } | null>(null)
  useLayoutEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const listRef = useRef<HTMLDivElement | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const menuId = useId()
  const activeRef = useRef<HTMLButtonElement | null>(null)

  const visible = useMemo(() => models.filter((m) => m.id === current || m.visible !== false), [models, current, locale])
  const groups = useMemo(() => groupModelsByProvider(filterModels(visible, query)), [visible, query, locale])

  // 扁平索引：键盘导航在分组视图上移动的是"展开后的行序"，不是分组序。
  const rows = useMemo(() => groups.flatMap((g) => g.models), [groups, locale])
  const showSearch = visible.length > SEARCH_THRESHOLD

  useLayoutEffect(() => {
    if (!menuVisible) return
    const root = menuRef.current
    const input = root?.querySelector<HTMLInputElement>('input')
    ;(input ?? root)?.focus()
  }, [menuVisible, showSearch])

  useEffect(() => {
    const index = rows.findIndex((m) => m.id === current)
    setActive(index >= 0 ? index : 0)
  }, [rows, current])

  useLayoutEffect(() => {
    const el = activeRef.current
    const box = listRef.current
    if (!el || !box) return
    const top = el.offsetTop
    const bottom = top + el.offsetHeight
    if (top < box.scrollTop) box.scrollTop = top - 4
    else if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight + 4
  }, [active])

  const commit = useCallback(
    async (id: string) => {
      const state = useAppStore.getState()
      const owner = sessionId
      if (!mounted.current || state.activeSessionId !== owner || pendingSave.current || state.modelSaves[modelSaveKey(owner)]) return
      const selected = owner ? sessionModelOf(state.sessions.find(session => session.id === owner), state.defaultModel) : current
      if (id === selected) {
        onClose()
        return
      }
      pendingSave.current = true
      setPending(true)
      menuRef.current?.focus()
      setModelFeedback(null)
      // 没有会话时改的是全局默认 —— 否则在"还没有会话"的空白页上
      // 用户点模型却毫无反应，会以为按钮坏了。
      try {
        const failure = owner ? await state.setSessionModel(owner, id) : await state.setDefaultModel(id)
        if (!mounted.current || useAppStore.getState().activeSessionId !== owner) return
        if (failure) setModelFeedback({ id, message: failure.copy ?? failure.message,
          unconfirmed: modelWriteUnconfirmed(failure) })
        else onClose()
      } catch (failure) {
        if (mounted.current && useAppStore.getState().activeSessionId === owner) setModelFeedback({ id,
          message: failureText(failure), unconfirmed: true })
      } finally {
        pendingSave.current = false
        if (mounted.current) setPending(false)
      }
    },
    [current, onClose, sessionId],
  )

  const currentInfo = rows.find((m) => m.id === current) ?? models.find((m) => m.id === current)
  const efforts = currentInfo?.reasoning_efforts ?? []

  // Only events owned by the focused popup may navigate or commit a model.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const targetElement = event.target instanceof HTMLElement ? event.target : null
      if (!targetElement || !menuRef.current?.contains(targetElement) || event.defaultPrevented) return
      if (event.isComposing || event.keyCode === 229) {
        if (event.key === 'Enter') event.preventDefault()
        return
      }
      if (event.ctrlKey || event.metaKey || event.altKey || (event.shiftKey && event.key !== 'Tab')) return
      if (routing?.models.length) {
        if (event.key === 'Escape') { event.preventDefault(); onClose() }
        return
      }
      if ((pendingSave.current || useAppStore.getState().modelSaves[modelSaveKey(useAppStore.getState().activeSessionId)])
        && !['Escape', 'Tab'].includes(event.key)) return
      const localButton = targetElement?.closest('button')
      const auxiliaryButton = localButton && menuRef.current?.contains(localButton) && localButton.getAttribute('role') !== 'option'
      const readingFeedback = targetElement?.closest('[role="alert"]')
      if (event.key !== 'Escape' && (auxiliaryButton || readingFeedback)) return
      if (targetElement.tagName === 'INPUT' && ['Home', 'End'].includes(event.key)) return
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault()
        if (!rows.length) return
        const options = Array.from(menuRef.current.querySelectorAll<HTMLButtonElement>('[role="option"]'))
        const focusedIndex = options.indexOf(localButton as HTMLButtonElement)
        const origin = focusedIndex >= 0 ? focusedIndex : active
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1
          : (origin + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length
        setActive(next)
        if (focusedIndex >= 0) options[next]?.focus()
      } else if (event.key === 'Tab') {
        // Navigation never commits a model selection. Unmount the portal
        // before native focus traversal, with onClose restoring its trigger.
        flushSync(onClose)
      } else if (event.key === 'Enter') {
        if (localButton?.getAttribute('role') === 'option') return
        event.preventDefault()
        const target = rows[active]
        if (target) void commit(target.id)
      } else if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [active, commit, onClose, rows, routing?.models.length])

  if (routing?.models.length) return <div ref={menuRef} tabIndex={-1} className={cn('space-y-3 rounded-xl border border-line bg-canvas p-4 text-xs shadow-xl', className)} role="dialog" aria-label={t("ui.components.chat.ModelPicker.fb274bc4cd34")}>
    <p className="font-medium">{t("ui.models.routingCycle", { cycle: routing.cycle })}</p>
    {routing.models.filter((m) => m.enabled).map((m, index) => <div key={m.id} className="space-y-1"><p>{index + 1}. {m.model}</p><div className="flex flex-wrap gap-2">{m.keys.map((k, ki) => <span key={k.id} className={k.available ? (k.current ? 'text-ok' : 'text-muted') : 'text-danger'}>{t(k.in_use ? "ui.models.routeKeyInUse" : "ui.models.routeKey", { index: ki + 1, mask: k.mask })}</span>)}</div></div>)}
    <button type="button" className="text-brand" onClick={() => { onClose(); useAppStore.getState().setSettingsOpen(true, 'models') }}>{t("ui.components.chat.ModelPicker.a4121f7399fb")}</button>
    <button type="button" className="ml-3 text-muted" onClick={onClose}>{t("ui.components.chat.ModelPicker.3fd47edce45b")}</button>
  </div>

  return (
    <div
      ref={menuRef}
      id={menuId}
      role="listbox"
      aria-label={title}
      aria-activedescendant={rows[active] ? `${menuId}-option-${active}` : undefined}
      tabIndex={-1}
      className={cn(
        'overlay-panel w-model-menu max-w-full overflow-hidden rounded-xl bg-surface focus:outline-none',
        className,
      )}
    >
      {/* 头部保留所选模型及其思考强度，请求实际使用值在生成摘要中呈现。 */}
      <header className="border-b border-line px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Cpu className="h-3.5 w-3.5 shrink-0 text-brand" />
          <span className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">
            {title}
          </span>
          {pending ? <span className="text-2xs text-faint">{t("ui.components.chat.ModelPicker.ad0196c88aaf")}</span> : null}
        </div>
        {!pending ? <p className="mt-1 text-xs leading-reading text-muted [overflow-wrap:anywhere]">{t(!sessionId ? 'ui.models.defaultScope' : !session ? 'ui.models.scopeUnknown' : 'ui.models.conversationScope')}</p> : null}
        {currentInfo?.description ? (
          <p className="mt-1 line-clamp-2 text-2xs leading-relaxed text-faint">{currentInfo.description}</p>
        ) : null}
        {modelFeedback ? <div role="alert" aria-label={t("ui.components.chat.ModelPicker.5f93c9daad89")} className="mt-2 space-y-1 text-xs text-danger [overflow-wrap:anywhere]">
          <p>{modelFeedback.unconfirmed ? t("ui.components.chat.ModelPicker.25031a497c2c") : t("ui.components.chat.ModelPicker.3a4d6bbf3d4c")}</p>
          <p tabIndex={0} className="max-h-24 overflow-y-auto">{noticeText(modelFeedback.message)}</p>
          <button type="button" disabled={pending} onClick={() => void commit(modelFeedback.id)} className="text-brand underline disabled:opacity-50">{t("ui.models.retrySwitch", { model: modelDisplayName(modelFeedback.id, models, t('ui.models.unavailableName')) })}</button>
        </div> : null}
        <div className="mt-1 flex items-center gap-3 text-2xs text-faint">
          {currentInfo?.context_window ? (
            <span>{t("ui.models.contextSize", { count: formatNumber(currentInfo.context_window) })}</span>
          ) : null}
          {currentInfo?.max_output ? <span>{t("ui.models.outputSize", { count: formatNumber(currentInfo.max_output) })}</span> : null}
        </div>

        {efforts.length > 0 ? (
          <div className="mt-2 space-y-1.5">
            <div className="flex items-center gap-1.5">
              <Gauge className="h-3 w-3 shrink-0 text-faint" />
              <span className="whitespace-nowrap text-2xs text-faint">{t("ui.components.chat.ModelPicker.8cf0105d35ee")}</span>
            </div>
            <div role="group" aria-label={t("ui.components.chat.ModelPicker.8cf0105d35ee")} className="flex flex-wrap items-center gap-0.5 rounded-lg border border-line bg-raised p-0.5">
              {sessionId ? <button type="button" onClick={() => void setReasoningEffort(null, true)} aria-pressed={inheritedEffort}
                className={cn('rounded-md px-1.5 py-0.5 text-2xs transition-colors', inheritedEffort ? 'bg-brand/15 text-brand' : 'text-muted hover:text-ink')}>{t('ui.components.chat.ModelPicker.2fb47e73f109')}</button> : null}
              <button type="button" disabled={!sessionId && creatingSession} onClick={() => void setReasoningEffort(null)} aria-pressed={(!sessionId || !inheritedEffort) && effort == null}
                className={cn('rounded-md px-1.5 py-0.5 text-2xs transition-colors disabled:opacity-50', (!sessionId || !inheritedEffort) && effort == null ? 'bg-brand/15 text-brand' : 'text-muted hover:text-ink')}>{t("ui.components.chat.ModelPicker.9a36b187cea4")}</button>
              {efforts.map((item) => (
                <button
                  key={item}
                  type="button"
                  disabled={!sessionId && creatingSession}
                  onClick={() => void setReasoningEffort(item)}
                  aria-pressed={(!sessionId || !inheritedEffort) && effort === item}
                  className={cn(
                    'rounded-md px-1.5 py-0.5 text-2xs capitalize transition-colors',
                    (!sessionId || !inheritedEffort) && effort === item ? 'bg-brand/15 text-brand' : 'text-muted hover:text-ink',
                    'disabled:opacity-50',
                  )}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {efforts.length > 0 ? <p className="mt-1 text-2xs text-faint">{!sessionId ? t("ui.components.chat.ModelPicker.80cf6df84c71") : inheritedEffort ? t("ui.components.chat.ModelPicker.8b6340d797b6", {p0: (effort ?? t("ui.components.chat.ModelPicker.9a36b187cea4"))}) : effort == null ? t("ui.components.chat.ModelPicker.134b6ccf3343") : t("ui.components.chat.ModelPicker.73ed98a2b58f", {p0: (effort)})}</p> : null}
        {reasoningSave?.pending ? <p role="status" className="mt-2 text-2xs text-muted">{t("ui.components.chat.ModelPicker.7fe024e948c8")}</p> : null}
        {reasoningSave?.error ? <div role="alert" aria-label={t("ui.components.chat.ModelPicker.49fd236d4f7a")} className="mt-2 space-y-1 text-2xs text-danger [overflow-wrap:anywhere]">
          <p>{reasoningSave.unconfirmed ? t("ui.components.chat.ModelPicker.0253e2fec0d4") : t("ui.components.chat.ModelPicker.b4946a2d4971")}</p>
          <p className="max-h-24 overflow-y-auto" tabIndex={0}>{noticeText(reasoningSave.error)}</p>
          {defaultReasoningSave?.error && inheritedEffort ? <button type="button" disabled={creatingSession || defaultReasoningSave.pending}
            onClick={() => void setReasoningEffort(null)} className="text-brand underline disabled:opacity-50">{t('ui.models.resetReasoning')}</button> : null}
        </div> : null}
      </header>

      {showSearch ? (
        <div className="flex items-center gap-2 border-b border-line px-3 py-2">
          <Search className="h-3.5 w-3.5 shrink-0 text-faint" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("ui.components.chat.ModelPicker.8ec7b052f694")}
            aria-label={t("ui.components.chat.ModelPicker.93a8734cce30")}
            aria-controls={menuId}
            aria-activedescendant={rows[active] ? `${menuId}-option-${active}` : undefined}
            className="min-w-0 flex-1 rounded-md border border-line bg-raised px-2 py-1 text-xs text-ink placeholder:text-faint focus:border-brand/50 focus:outline-none"
          />
          {query ? (
            <button type="button" onClick={() => setQuery('')} aria-label={t("ui.components.chat.ModelPicker.bce2377283c2")} className="text-faint hover:text-ink">
              <X className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
      ) : null}

      {/* relative 不能省：下面按 offsetTop 做 scrollIntoView，offsetParent 必须是
          这个滚动容器本身，否则量到的是更外层定位祖先，选中项永远滚不进视野。 */}
      <div ref={listRef} className="scroll-fade-y relative max-h-model-menu overflow-y-auto p-1.5">
        {rows.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs text-faint">{t("ui.components.chat.ModelPicker.4b5e0ae1288a")}</p>
        ) : (
          groups.map((group) => (
            <div key={JSON.stringify(group.provider)} className="mb-1 last:mb-0">
              <p className="px-2 py-1 text-2xs font-semibold text-faint">{group.provider ?? t('ui.models.unspecifiedProvider')}</p>
              {group.models.map((model) => {
                const index = rows.indexOf(model)
                const on = index === active
                const selected = model.id === current
                return (
                  <button
                    key={model.id}
                    ref={on ? activeRef : undefined}
                    type="button"
                    role="option"
                    disabled={pending}
                    id={`${menuId}-option-${index}`}
                    aria-selected={selected}
                    onFocus={() => setActive(index)}
                    onMouseMove={(event) => {
                      setActive(index)
                      const focused = document.activeElement
                      // Preserve editing/virtual focus, but do not leave an
                      // already-focused option behind the hovered highlight.
                      if (focused !== event.currentTarget && focused?.getAttribute('role') === 'option'
                        && menuRef.current?.contains(focused)) event.currentTarget.focus({ preventScroll: true })
                    }}
                    onClick={() => void commit(model.id)}
                    className={cn(
                      'flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition-colors',
                      on ? 'bg-brand/[0.12]' : 'hover:bg-raised/60',
                    )}
                  >
                    <Check className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', selected ? 'text-brand' : 'text-transparent')} />
                    <span className="min-w-0 flex-1">
                      <span className={cn('block truncate text-xs', selected ? 'font-semibold text-ink' : 'text-muted')}>
                        {model.label}
                      </span>
                      <span className="mt-0.5 flex items-center gap-2 text-2xs text-faint">
                        {model.supports_reasoning ? <span className="text-brand/80">{t("ui.components.chat.ModelPicker.886028f72ba3")}</span> : null}
                        {model.context_window ? <span>{t("ui.models.contextSize", { count: formatNumber(model.context_window) })}</span> : null}
                      </span>
                    </span>
                  </button>
                )
              })}
            </div>
          ))
        )}
      </div>

      <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-1.5 text-2xs text-faint">
        <span className="inline-flex items-center gap-1"><Kbd>↑</Kbd><Kbd>↓</Kbd><span>{t("ui.components.chat.ModelPicker.c11330b85234")}</span></span>
        <span className="inline-flex items-center gap-1"><Kbd>Enter</Kbd><span>{t("ui.components.chat.ModelPicker.36f33adaf094")}</span></span>
        <span className="inline-flex items-center gap-1"><Kbd>Esc</Kbd><span>{t("ui.components.chat.ModelPicker.3fd47edce45b")}</span></span>
        <span className="basis-full">{t("ui.components.chat.ModelPicker.95049da57a96")}</span>
      </footer>
    </div>
  )
}
