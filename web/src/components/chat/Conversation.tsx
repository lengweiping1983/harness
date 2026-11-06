import { HarnessLogo } from '@/components/ui/HarnessLogo'
import { noticeText } from '@/lib/noticeText'
import { observeMessageSelectionCopy } from '@/lib/copyMessageSelection'
import { messageNavigationEntries, type MessageNavigationEntry } from '@/lib/messageNavigation'
import { MessageNavigator } from './MessageNavigator'
import { t, useLocale } from '@/i18n'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ListOrdered, RotateCw, WifiOff } from 'lucide-react'
import type { Block, QuestionBlock } from '@/types'
import { api, unwrap } from '@/lib/api'
import { cn, createDateHeadingFormatter } from '@/lib/utils'
import { useDraftStore } from '@/store/drafts'
import { replyFeedback } from '@/lib/replyFeedback'
import { useAppStore } from '@/store/app'
import { useChatStore, type LoadedHistory } from '@/store/chat'
import { Button, ShowMoreControl } from '@/components/ui/primitives'
import type { BlockContext } from './MessageList'
import {DeferredMessageRow as MessageRow} from './DeferredMessageRow'
import { fileChangeRows } from '@/lib/fileChanges'
import { conversationRows, type TurnActivity } from '@/lib/turnActivity'
import { groupToolRuns } from '@/lib/toolRuns'
import { completionRows, type CompletedConversationRow } from '@/lib/turnCompletion'
import { TurnCompletionBar } from './blocks/TurnCompletionBar'
import { TurnElapsed } from './blocks/TurnElapsed'
import { ProcessGroupSummary, ProcessSummaryButton } from './blocks/ProcessSummary'
import { ConversationScenarios } from './ConversationScenarios'
import { readTurnDisclosure, rememberTurnDisclosures } from '@/lib/turnDisclosure'
import { appendedBlockCount } from '@/lib/conversationArrival'
import { FileChangesCard } from './blocks/FileChangesCard'
import { changeCardVisible, useChangeCards } from '@/store/changeCards'
import { clearPlanFeedback } from '@/lib/planFeedbackDraft'
import { clearQuestionDraft } from '@/lib/questionDraft'
import { assertQuestionAnswerAccepted, questionAnswerConfirmed, questionSubmissionFeedback, questionSubmissionKey, type QuestionSubmissionFeedback } from '@/lib/questionSubmission'

/**
 * 对话区。
 *
 * 长会话是 harness UI 的常态（几千条 tool/assistant block 很常见），
 * 所以走动态高度虚拟化：`measureElement` 量真实高度，Markdown / 代码块 /
 * 图表撑多高都能兜住，仅挂载可见范围和少量预加载行，避免重复的高度占位与测量。
 *
 * 三个体验细节决定它像不像成品：
 *   1. 粘底：只有用户本来就在底部时才自动跟随；往上一翻就停，
 *      免得正在读的结论被强行拽走。
 *   2. 跳转按钮：不在底部时右下角浮一个「回到最新」，带未读条数。
 *   3. 问题钉顶：等待回答的交互卡片固定在顶部，不沉进历史里。
 */

type Item =
  | { kind: 'history'; id: string }
  | { kind: 'day'; id: string; label: string }
  | CompletedConversationRow

type NavigationTarget = { sessionId: string | null } & ({ id: string } | { sourceSeq: number })

const EMPTY_TURNS: Record<number, TurnActivity> = {}

export function Conversation({
  turns = EMPTY_TURNS,
  readOnly = false,
  answerQuestion,
  blocks,
  running,
  streaming,
  connection,
  history,
  onRefreshHistory,
  onReconnect,
}: {
  turns?: Record<number, TurnActivity>
  readOnly?: boolean
  answerQuestion?: (questionId: string, answers: unknown) => Promise<void>
  blocks: Block[]
  running: boolean
  streaming: boolean
  connection: string
  history?: LoadedHistory | null
  onReconnect?: () => void
  onRefreshHistory?: () => void
}) {
  const locale = useLocale()

  const parentRef = useRef<HTMLDivElement | null>(null)
  const [toolRunState, setToolRunState] = useState<{ sessionId: string | null; values: Record<string, boolean> }>({ sessionId: null, values: {} })
  const [atBottom, setAtBottom] = useState(true)
  const following = useRef(true)
  const lastScrollTop = useRef(0)
  const resumeIntent = useRef(false)
  const layoutFrame = useRef<number | null>(null)
  // 历史消息和轮次摘要会分批懒加载；逐项贴底会造成重新进入时连续闪跳。
  const initialRestorePending = useRef(false)
  const initialRestoreTimer = useRef<number | null>(null)
  const [restoreSettled, setRestoreSettled] = useState(false)
  const touchY = useRef<number | null>(null)
  const olderFrame = useRef<number | null>(null)
  const setFollowing = useCallback((value: boolean) => {
    following.current = value
    setAtBottom(value)
  }, [])
  const [hasContentBelow, setHasContentBelow] = useState(false)
  const [atTop, setAtTop] = useState(true)
  const [unread, setUnread] = useState(0)
  const answerRequests = useRef(new Set<string>())
  const [busyQuestionKeys, setBusyQuestionKeys] = useState<ReadonlySet<string>>(() => new Set())
  const [answerFeedback, setAnswerFeedback] = useState<Record<string, QuestionSubmissionFeedback | undefined>>({})
  const toast = useAppStore((s) => s.toast)
  const sessionId = useAppStore((s) => s.activeSessionId)
  useEffect(() => {
    const scope = parentRef.current
    if (scope) return observeMessageSelectionCopy(scope)
  }, [sessionId])
  const toolRunChoices = useMemo(() => toolRunState.sessionId === sessionId ? toolRunState.values : {}, [toolRunState, sessionId])
  const latestTurn = Object.keys(turns).reduce((latest, turn) => Math.max(latest, Number(turn)), 0)
  const queuedEmpty = useChatStore((state): 'none' | 'paused' | 'environment' | 'queued' => {
    const chat = sessionId ? state.sessions[sessionId] : undefined
    return !chat?.queued ? 'none' : chat.queuePaused ? 'paused' : chat.queueWaitingReason === 'environment' ? 'environment' : 'queued'
  })
  const [activityOverrides, setActivityOverrides] = useState<{ sessionId: string | null; values: Record<number, boolean> }>({ sessionId, values: {} })
  const activityChoices = useMemo(() => ({ ...readTurnDisclosure(sessionId, turns),
    ...(activityOverrides.sessionId === sessionId ? activityOverrides.values : {}) }), [sessionId, turns, activityOverrides, locale])
  useEffect(() => {
    if (activityOverrides.sessionId === sessionId) rememberTurnDisclosures(sessionId, turns, activityOverrides.values)
  }, [sessionId, turns, activityOverrides])
  const toolRunIdentities = useRef({ sessionId, aliases: new Map<string, string>() })
  const arrival = useRef<{ sessionId: string | null; generation?: number; tail?: string }>({ sessionId })
  const disclosureAnchor = useRef<{ id: string; y: number } | null>(null)
  const anchor = useRef<{ id: string; y: number; focus: boolean } | null>(null)
  const olderIntent = useRef(false)
  const lastRevision = useRef(history?.revision ?? 0)

  const sending = useDraftStore(state => Boolean(sessionId && state.submissions[sessionId]?.some(item => item.action === 'send' && item.status === 'sending')))
  const feedback = useChatStore(state => replyFeedback({ blocks: sessionId ? state.sessions[sessionId]?.blocks ?? blocks : blocks, running, streaming, sending, generation: sessionId ? state.sessions[sessionId]?.generation : null }))
  const feedbackText = feedback ? t(feedback) : null
  const presentedBlocks = useMemo<Block[]>(() => feedbackText ? [...blocks, { id: 'live-feedback:' + sessionId, kind: 'notice', at: blocks.at(-1)?.at ?? Date.now(), text: feedbackText, tone: 'info' }] : blocks, [blocks, feedbackText, sessionId])

  /* ---------- 问题卡片：待回答的钉在顶部 ---------- */
  const pending = useMemo(
    () => blocks.filter((b): b is QuestionBlock => b.kind === 'question' && !b.answered),
    [blocks, locale],
  )
  const pendingIds = useMemo(() => new Set(pending.map((b) => b.id)), [pending, locale])
  const body = useMemo(
    () => (pendingIds.size ? presentedBlocks.filter((b) => !pendingIds.has(b.id)) : presentedBlocks),
    [presentedBlocks, pendingIds, locale],
  )

  /* ---------- 打平成带日期分隔的虚拟列表 ---------- */
  const changeChoices = useChangeCards(s => s.choices)
  const changeTarget = useChangeCards(s => s.target)
  const changeRows = useMemo(() => fileChangeRows(body, running).map(row => row.kind === 'changes' && row.turn && turns[row.turn]?.endSeq !== undefined ? { ...row, pending: false } : row), [body, running, turns, locale])
  useEffect(() => {
    if (sessionId) useChangeCards.getState().remember(sessionId, changeRows.filter(row => row.kind === 'changes').at(-1))
  }, [sessionId, changeRows])
  const items = useMemo<Item[]>(() => {
    if (toolRunIdentities.current.sessionId !== sessionId) toolRunIdentities.current = { sessionId, aliases: new Map() }
    const loadedIds = new Set(body.map(block => block.id))
    for (const id of toolRunIdentities.current.aliases.keys()) if (!loadedIds.has(id)) toolRunIdentities.current.aliases.delete(id)
    const out: Item[] = []
    if (history && (history.has_more || history.loading || history.error || history.resetRequired)) out.push({ kind: 'history', id: 'conversation-history' })
    const formatDay = createDateHeadingFormatter()
    let lastDay = ''
    for (const row of completionRows(groupToolRuns(conversationRows(changeRows.filter(row => row.kind !== 'changes' || changeCardVisible(row, sessionId, changeChoices)), turns, activityChoices), toolRunChoices, toolRunIdentities.current.aliases), turns)) {
      const day = formatDay(row.kind === 'block' ? row.block.at : row.at)
      if (day !== lastDay) {
        out.push({ kind: 'day', id: `day-${day}-${row.id}`, label: day })
        lastDay = day
      }
      out.push(row)
    }
    return out
  }, [body, changeRows, changeChoices, history?.has_more, history?.loading, history?.error, history?.resetRequired, turns, activityChoices, toolRunChoices, sessionId, locale])

  const navigationEntries = useMemo(() => messageNavigationEntries(items, history), [items, history])
  const navigationRead = useRef<Promise<void> | null>(null)
  const navigationTarget = useRef<NavigationTarget | null>(null)
  useEffect(() => () => { navigationTarget.current = null }, [sessionId])

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 108,
    overscan: 10,
    getItemKey: (index) => items[index]?.id ?? index,
  })

  const virtualItems = virtualizer.getVirtualItems()
  const alignNavigationTarget = useCallback(() => {
    const target = navigationTarget.current
    if (!target || target.sessionId !== sessionId) return
    const index = items.findIndex(row => 'id' in target ? row.id === target.id
      : row.kind === 'block' && row.block.kind === 'user' && row.block.sourceSeq === target.sourceSeq)
    if (index < 0) return
    const node = parentRef.current
    const id = items[index].id
    const row = node && Array.from(node.querySelectorAll<HTMLElement>('[data-row-id]')).find(row => row.dataset.rowId === id)
    // 已挂载行使用当前几何位置，避免历史控制条变高或变矮后沿用旧高度。
    // 未挂载时保留同一目标，等虚拟行提交后校准，不另建延时重试任务。
    if (node && row) {
      navigationTarget.current = null
      virtualizer.scrollToOffset(node.scrollTop + row.getBoundingClientRect().top - node.getBoundingClientRect().top)
    } else virtualizer.scrollToIndex(index, { align: 'start' })
  }, [items, sessionId, virtualizer])
  useLayoutEffect(alignNavigationTarget, [alignNavigationTarget, virtualItems])

  const jumpToMessage = async (entry: MessageNavigationEntry) => {
    olderIntent.current = false
    disclosureAnchor.current = null
    anchor.current = null
    resumeIntent.current = false
    setFollowing(false)
    navigationTarget.current = null
    if (entry.index >= 0) {
      navigationTarget.current = { sessionId, id: entry.id }
      alignNavigationTarget()
      return
    }
    if (!sessionId || entry.sourceSeq === undefined) return
    const target = { sessionId, sourceSeq: entry.sourceSeq }
    navigationTarget.current = target
    const initial = useChatStore.getState().get(sessionId).history
    while (navigationTarget.current === target && useAppStore.getState().activeSessionId === sessionId) {
      const state = useChatStore.getState().get(sessionId)
      if (state.blocks.some(block => block.kind === 'user' && block.sourceSeq === target.sourceSeq)) return
      const page = state.history
      if (!page || page.generation !== initial?.generation || !page.has_more || page.resetRequired) break
      if (page.loading) {
        if (!navigationRead.current) break
        await navigationRead.current
        continue
      }
      const read = useChatStore.getState().loadOlder(sessionId)
      navigationRead.current = read
      await read
      if (navigationRead.current === read) navigationRead.current = null
      const updated = useChatStore.getState().get(sessionId).history
      if (updated?.start === page.start) {
        if (navigationTarget.current === target && useAppStore.getState().activeSessionId === sessionId && updated.error) toast({ title: noticeText(updated.error), tone: 'danger' })
        break
      }
    }
    if (navigationTarget.current === target) navigationTarget.current = null
  }

  useEffect(() => {
    if (!changeTarget || changeTarget.sessionId !== sessionId) return
    const index = items.findIndex(row => row.id === changeTarget.id)
    if (index >= 0) virtualizer.scrollToIndex(index, { align: 'center' })
  }, [changeTarget, sessionId])

  const loadOlder = useCallback(() => {
    const node = parentRef.current
    if (!sessionId || !node || !history?.has_more || history.loading || history.resetRequired) return
    const top = node.getBoundingClientRect().top
    const visible = Array.from(node.querySelectorAll<HTMLElement>('[data-anchor-row]')).find((row) =>
      row.getBoundingClientRect().bottom > top && row.getBoundingClientRect().top < top + node.clientHeight)
    anchor.current = visible ? { id: visible.dataset.rowId!, y: visible.getBoundingClientRect().top - top,
      focus: node.contains(document.activeElement) } : null
    olderIntent.current = false
    resumeIntent.current = false
    setFollowing(false)
    void useChatStore.getState().loadOlder(sessionId)
  }, [sessionId, history, setFollowing])

  // 等虚拟列表提交当前可见行后再抓取历史锚点，快速 Home/滚轮跳转也能保位。
  const scheduleOlder = useCallback(() => {
    if (olderFrame.current !== null) return
    olderFrame.current = requestAnimationFrame(() => {
      olderFrame.current = null
      if (olderIntent.current && (parentRef.current?.scrollTop ?? Infinity) < 140) loadOlder()
    })
  }, [loadOlder])
  useEffect(() => () => {
    if (olderFrame.current !== null) cancelAnimationFrame(olderFrame.current)
    olderFrame.current = null
  }, [sessionId])

  // Restore the exact visible row after prepending, including dynamic Markdown
  // heights. Loading older records is not an incoming/unread message.
  useLayoutEffect(() => {
    if (arrival.current.sessionId !== sessionId) return
    if ((history?.revision ?? 0) === lastRevision.current) return
    lastRevision.current = history?.revision ?? 0
    const saved = anchor.current, node = parentRef.current
    anchor.current = null
    if (!saved || !node) return
    const index = items.findIndex((item) => item.id === saved.id)
    if (index < 0) return
    virtualizer.scrollToIndex(index, { align: 'start' })
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        if (arrival.current.sessionId !== sessionId) return
        const row = Array.from(node.querySelectorAll<HTMLElement>('[data-anchor-row]')).find((row) => row.dataset.rowId === saved.id)
        if (row) virtualizer.scrollToOffset(node.scrollTop + row.getBoundingClientRect().top - node.getBoundingClientRect().top - saved.y)
        if (saved.focus && !node.contains(document.activeElement)) node.focus({ preventScroll: true })
      })
    })
    return () => cancelAnimationFrame(frame)
    // 历史修订是唯一的恢复触发点；流式输出重算 items 时不能取消尚未完成的双帧定位。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [history?.revision, virtualizer, sessionId])

  /* ---------- 粘底 ---------- */
  // 所有自动跟随共用一帧；回调执行时再确认意图，向上浏览立即生效。
  // 直接定位已渲染容器底部，避免虚拟列表的索引追踪持续争抢滚动位置。
  const scheduleLayout = useCallback(() => {
    if (layoutFrame.current !== null) return
    layoutFrame.current = requestAnimationFrame(() => {
      layoutFrame.current = null
      const node = parentRef.current
      if (!node) return
      const end = Math.max(0, node.scrollHeight - node.clientHeight)
      if (initialRestorePending.current) {
        // 用固定的短暂收敛窗口合并懒加载卡片的多次高度变化；计时器结束后只恢复一次。
        setHasContentBelow(end - node.scrollTop > 2)
        setAtTop(node.scrollTop < 8)
        return
      }
      if (following.current && !disclosureAnchor.current && Math.abs(end - node.scrollTop) > 1) {
        node.scrollTop = end
      }
      setHasContentBelow(end - node.scrollTop > 2)
      setAtTop(node.scrollTop < 8)
    })
  }, [])

  const onScroll = useCallback(() => {
    const node = parentRef.current
    if (!node) return
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight
    const movedDown = node.scrollTop > lastScrollTop.current
    lastScrollTop.current = node.scrollTop
    setHasContentBelow(distance > 2)
    // 离底部很近不代表愿意继续跟随；只有向下滚到实际底部才恢复。
    if (!following.current && resumeIntent.current && movedDown && distance <= 2) {
      olderIntent.current = false
      setFollowing(true)
      setUnread(0)
    }
    setAtTop(node.scrollTop < 8)
    if (olderIntent.current && node.scrollTop < 140 && !history?.error && !history?.loading) scheduleOlder()
  }, [history?.error, history?.loading, scheduleOlder, setFollowing])

  // 观察器只随空/非空结构变化重建，不随每个流式片段反复断开和订阅。
  const hasItems = items.length > 0
  useLayoutEffect(() => {
    const node = parentRef.current
    if (!node) return
    const observer = new ResizeObserver(scheduleLayout)
    observer.observe(node)
    if (node.firstElementChild) observer.observe(node.firstElementChild)
    scheduleLayout()
    return () => {
      observer.disconnect()
      if (layoutFrame.current !== null) cancelAnimationFrame(layoutFrame.current)
      layoutFrame.current = null
    }
  }, [hasItems, scheduleLayout])

  const browseOlder = () => {
    navigationTarget.current = null
    // 滚轮期间不写 scrollTop，保留浏览器原生滚动及触控板惯性。
    disclosureAnchor.current = null
    resumeIntent.current = false
    setFollowing(false)
    olderIntent.current = true
    if ((parentRef.current?.scrollTop ?? Infinity) < 140 && !history?.error) scheduleOlder()
  }

  useLayoutEffect(() => {
    const previous = arrival.current
    const changedSession = previous.sessionId !== sessionId
    const sameSnapshot = !changedSession && previous.generation === history?.generation
    const added = sameSnapshot ? appendedBlockCount(previous.tail, blocks) : 0
    arrival.current = { sessionId, generation: history?.generation, tail: blocks.at(-1)?.id }
    if (changedSession) {
      anchor.current = null
      disclosureAnchor.current = null
      olderIntent.current = false
      lastRevision.current = history?.revision ?? 0
      setUnread(0)
      setFollowing(true)
      resumeIntent.current = false
      touchY.current = null
      lastScrollTop.current = 0
      scheduleLayout()
    } else if (added && !following.current) setUnread(n => n + added)
  }, [blocks, sessionId, history?.generation, history?.revision, scheduleLayout, setFollowing])

  // 首次挂载和每次重新进入都需要同一段布局收敛窗口；把它放在独立
  // 的会话 effect 中，避免长对话每次收到新内容时重新计时。
  useLayoutEffect(() => {
    initialRestorePending.current = true
    setRestoreSettled(false)
    const node = parentRef.current
    if (node) node.scrollTop = Math.max(0, node.scrollHeight - node.clientHeight)
    if (initialRestoreTimer.current !== null) window.clearTimeout(initialRestoreTimer.current)
    initialRestoreTimer.current = window.setTimeout(() => {
      initialRestoreTimer.current = null
      initialRestorePending.current = false
      setRestoreSettled(true)
      scheduleLayout()
    }, 1200)
    return () => {
      if (initialRestoreTimer.current !== null) window.clearTimeout(initialRestoreTimer.current)
      initialRestoreTimer.current = null
    }
  }, [sessionId, scheduleLayout])

  useEffect(() => {
    scheduleLayout()
  }, [items, scheduleLayout])

  const holdDisclosure = (id: string, button: HTMLButtonElement) => {
    const node = parentRef.current, row = button.closest<HTMLElement>('[data-row-id]')
    if (node && row) disclosureAnchor.current = { id, y: row.getBoundingClientRect().top - node.getBoundingClientRect().top }
    // Reading an expanded process is an explicit decision to stop following new output.
    setFollowing(false)
    resumeIntent.current = false
    olderIntent.current = false
  }

  useLayoutEffect(() => {
    const saved = disclosureAnchor.current, node = parentRef.current
    if (!saved || !node) return
    const index = items.findIndex(item => item.id === saved.id)
    if (index < 0) { disclosureAnchor.current = null; return }
    const findRow = () => Array.from(node.querySelectorAll<HTMLElement>('[data-row-id]')).find(row => row.dataset.rowId === saved.id)
    if (!findRow()) virtualizer.scrollToIndex(index, { align: 'start' })
    const restore = () => {
      if (disclosureAnchor.current !== saved) return
      const row = findRow()
      if (row) virtualizer.scrollToOffset(node.scrollTop + row.getBoundingClientRect().top - node.getBoundingClientRect().top - saved.y)
    }
    restore()
    let frame = requestAnimationFrame(() => {
      restore()
      frame = requestAnimationFrame(() => { restore(); if (disclosureAnchor.current === saved) disclosureAnchor.current = null })
    })
    return () => cancelAnimationFrame(frame)
  }, [items, virtualizer])

  const jumpToLatest = useCallback(() => {
    navigationTarget.current = null
    olderIntent.current = false
    disclosureAnchor.current = null
    resumeIntent.current = false
    setFollowing(true)
    setUnread(0)
    scheduleLayout()
  }, [scheduleLayout, setFollowing])

  /* ---------- 回答问题 ---------- */
  const onAnswer = useCallback(
    async (questionId: string, answers: unknown) => {
      if (readOnly && !answerQuestion) return
      const key = questionSubmissionKey(sessionId, questionId)
      // Acquire synchronously, before React renders disabled controls. Other
      // sessions may submit independently, but this question has one request.
      if (answerRequests.current.has(key)) return
      answerRequests.current.add(key)
      setBusyQuestionKeys(new Set(answerRequests.current))
      setAnswerFeedback((previous) => ({ ...previous, [key]: undefined }))
      try {
        if (answerQuestion) await answerQuestion(questionId, answers)
        else assertQuestionAnswerAccepted(await unwrap(api.answerQuestion(questionId, answers)))
        clearPlanFeedback(sessionId, questionId)
        clearQuestionDraft(sessionId, questionId)
        if (sessionId && !answerQuestion) {
          const state = useChatStore.getState()
          state.apply(sessionId, { type: 'question_answered', question_id: questionId, answers })
        }
      } catch (err) {
        // The committed live event may precede a lost HTTP acknowledgement.
        if (sessionId && questionAnswerConfirmed(useChatStore.getState().get(sessionId).blocks, questionId)) {
          clearPlanFeedback(sessionId, questionId)
          clearQuestionDraft(sessionId, questionId)
          return
        }
        const feedback = questionSubmissionFeedback(err)
        setAnswerFeedback((previous) => ({ ...previous, [key]: feedback }))
        toast({
          title: feedback.title,
          description: feedback.message,
          tone: 'danger',
        })
      } finally {
        answerRequests.current.delete(key)
        setBusyQuestionKeys(new Set(answerRequests.current))
      }
    },
    [toast, sessionId, readOnly, answerQuestion],
  )

  const context = useMemo<BlockContext>(
    () => ({ onAnswer, busyQuestionKeys, sessionId, answerFeedback, readOnly, questionReadOnly: readOnly && !answerQuestion }),
    [onAnswer, busyQuestionKeys, sessionId, answerFeedback, readOnly, answerQuestion, locale],
  )

  useEffect(() => { setActivityOverrides({ sessionId, values: {} }); setToolRunState({ sessionId, values: {} }) }, [sessionId])
  const disconnected = connection === 'reconnecting' || connection === 'closed'
  /** 空会话：hero 不再自己撑满中区，而是贴着输入卡组成一个整体。 */
  const isEmpty = items.length === 0

  return (
    <div
      className={cn('relative flex min-h-0 flex-col', isEmpty && !readOnly ? 'shrink-0' : 'flex-1')}
      data-empty={isEmpty ? '1' : '0'}
    >
      {pending.length > 0 ? (
        <div role="region" aria-label={t("ui.components.chat.Conversation.56897d5961b4")} className="relative z-20 flex min-h-0 max-h-conversation-dock shrink-0 flex-col gap-2 overflow-y-auto border-b border-line bg-canvas/85 px-4 pb-3 pt-3 backdrop-blur-md">
          {pending.map((block) => (
            <MessageRow key={block.id} block={block} context={context} />
          ))}
        </div>
      ) : null}

      {/* 底部渐隐：最后一段内容不硬切到输入卡片，而是淡入卡片背后。
          纯装饰，pointer-events-none 不吃任何事件，也不影响虚拟列表几何。

          呈现约束：
          1) 渐隐的终点色必须是 **surface**（中区舞台色），不能是 canvas。
             canvas 比 surface 深好几级，用它收尾会在对话区底部压出一条
             深于舞台的色带，并在色带尽头与输入区之间留下一道硬边 ——
             空会话时尤其明显：整屏被从中间切成两半。
          2) 空会话不画。空态下 hero 本来就没有"被切断的最后一段"，
             这条色带纯属凭空出现的一刀。 */}
      {isEmpty ? null : (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-3 bg-gradient-to-t from-surface to-transparent" />
      )}

      {/* 顶部渐隐：底部渐隐的镜像。滚动中的内容在顶缘柔和淡入，
          而不是被舞台边界硬切一刀。

          与底部那条的两点差异：
          1) 只在离开顶部后出现（atTop=false）。列表首条内容贴着顶边时
             蒙一层白色会把"第一条消息"永久压暗，像是加载残影。
          2) 起点色同样必须是 surface，理由同上。 */}
      {!isEmpty && !atTop ? (
        <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-12 bg-gradient-to-b from-surface to-transparent" />
      ) : null}

      <div
        ref={parentRef}
        role="region"
        aria-label={t("ui.components.chat.Conversation.b53654e6b37c")}
        data-history-start={history?.start}
        data-history-end={history?.end}
        data-history-loading={history?.loading ? '1' : '0'}
        tabIndex={0}
        onScroll={onScroll}
        onWheel={(event) => {
          if (event.ctrlKey) return
          if (event.deltaY < 0) browseOlder()
          else if (event.deltaY > 0) resumeIntent.current = true
        }}
        onTouchStart={(event) => { touchY.current = event.touches[0]?.clientY ?? null }}
        onTouchMove={(event) => {
          const y = event.touches[0]?.clientY
          if (y !== undefined && touchY.current !== null && y > touchY.current) browseOlder()
          else if (y !== undefined && touchY.current !== null && y < touchY.current) resumeIntent.current = true
          touchY.current = y ?? null
        }}
        onTouchEnd={() => { touchY.current = null }}
        onTouchCancel={() => { touchY.current = null }}
        onPointerDown={(event) => {
          // 拖动主滚动条时也停止跟随；正文内按钮和嵌套滚动区不改变主区意图。
          if (event.target === event.currentTarget) {
            resumeIntent.current = true
            olderIntent.current = true
            setFollowing(false)
          }
        }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget || event.nativeEvent.isComposing || event.keyCode === 229) return
          if (['End', 'PageDown', 'ArrowDown'].includes(event.key)) resumeIntent.current = true
          if (!['Home', 'PageUp', 'ArrowUp'].includes(event.key)) return
          event.preventDefault()
          browseOlder()
          const node = parentRef.current
          if (node) virtualizer.scrollToOffset(event.key === 'Home' ? 0 : Math.max(0, node.scrollTop - (event.key === 'PageUp' ? node.clientHeight : 40)))
        }}
        // 禁用浏览器的自动锚定；虚拟列表会在历史加载时按自己的锚点恢复，
        // 自动锚定叠加懒加载会把底部内容逐次推移，形成闪跳。
        style={{ overflowAnchor: 'none' }}
        className={cn('conversation-scroll min-h-0 flex-1 overflow-y-auto', !isEmpty && 'flex flex-col')}
      >
        {isEmpty ? (
          readOnly ? <div className="p-8 text-center text-sm text-muted">{t('ui.shared.emptyConversation')}</div> : queuedEmpty === 'none' ? <EmptyState /> : <QueuedEmptyState state={queuedEmpty} />
        ) : (
          <div
            className="relative mx-auto w-full shrink-0 px-6 py-6"
            style={{
              maxWidth: 'var(--chat-content-width)',
              height: virtualizer.getTotalSize(),
            }}
          >
            <div
              className="absolute left-0 top-0 w-full pl-9 pr-3 sm:pl-10 sm:pr-6"
              style={{ transform: `translateY(${virtualItems[0]?.start ?? 0}px)` }}
            >
              {virtualItems.map((item) => {
                const row = items[item.index]
                if (!row) return null
                if (row.kind === 'history') return <div key={row.id} data-index={item.index} data-row-id={row.id} ref={restoreSettled ? virtualizer.measureElement : undefined}
                  className="flex flex-col items-center gap-2 py-3 text-xs text-muted">
                  {history?.error ? <p role="alert" className="max-w-full text-center text-danger">{noticeText(history.error)}</p> : null}
                  {history?.resetRequired ? <Button size="sm" variant="secondary" icon={<RotateCw aria-hidden className="h-3.5 w-3.5" />} onClick={onRefreshHistory}>{t("ui.components.chat.Conversation.3014a4a1a292")}</Button>
                    : history?.has_more ? <ShowMoreControl className="w-auto" disabled={history.loading} onClick={loadOlder}>
                      {history.loading ? t("ui.components.chat.Conversation.3e8f9af3b2e4") : history.error ? t("ui.components.chat.Conversation.6d7f1def5670") : t("ui.showMore")}</ShowMoreControl>
                    : null}
                  <span role="status" aria-live="polite" className={history?.loading && !history.has_more && !history.resetRequired ? undefined : 'sr-only'}>{history?.loading ? t("ui.components.chat.Conversation.bca2a7dd358e") : history?.error ? '' : t("ui.components.chat.Conversation.64b4a67b6241")}</span>
                </div>
                if (row.kind === 'day') {
                  return (
                    <div
                      key={row.id}
                      data-index={item.index}
                      data-row-id={row.id}
                      ref={restoreSettled ? virtualizer.measureElement : undefined}
                      className="flex items-center gap-3 py-3"
                    >
                      <span className="h-px flex-1 bg-line" />
                      <span className="text-2xs font-medium tracking-wide text-faint">{row.label}</span>
                      <span className="h-px flex-1 bg-line" />
                    </div>
                  )
                }
                if (row.kind === 'tool-run') {
                  return <div key={row.id} data-index={item.index} data-row-id={row.id} data-anchor-row="" data-tool-run-turn={row.turn} ref={restoreSettled ? virtualizer.measureElement : undefined}>
                    <ProcessGroupSummary row={row} onClick={event => { holdDisclosure(row.id, event.currentTarget); setToolRunState(current => ({ sessionId, values: { ...(current.sessionId === sessionId ? current.values : {}), [row.id]: !row.open } })) }} />
                  </div>
                }
                if (row.kind === 'completion') return <div key={row.id} data-index={item.index} data-row-id={row.id} data-anchor-row="" ref={restoreSettled ? virtualizer.measureElement : undefined} className="py-1">
                  <TurnCompletionBar key={JSON.stringify([sessionId, row.endSeq])} row={row} sessionId={sessionId} />
                </div>
                if (row.kind === 'activity') {
                  return <div key={row.id} data-index={item.index} data-row-id={row.id} data-anchor-row="" ref={restoreSettled ? virtualizer.measureElement : undefined} className="border-b border-line py-2">
                    <ProcessSummaryButton disabled={!row.count} expanded={row.count ? row.open : undefined} aria-label={t("ui.components.chat.Conversation.c4ed97b7b12b", {p0: (row.turn)})} title={t("ui.components.chat.Conversation.5f933635623c", {p0: (row.count)})} onClick={event => { holdDisclosure(row.id, event.currentTarget); setActivityOverrides(current => ({ sessionId,
                      values: { ...(current.sessionId === sessionId ? current.values : {}), [row.turn]: !row.open } })) }}
                    >
                      <TurnElapsed key={JSON.stringify([sessionId, row.turn, row.activity.startSeq])} activity={row.activity} active={running && connection === 'open' && row.turn === latestTurn} />
                      {row.partial ? <span>{t("ui.components.chat.Conversation.1a8dde503030")}</span> : null}
                      <span className="sr-only">{t("ui.components.chat.Conversation.5f933635623c", {p0: row.count})}</span>
                    </ProcessSummaryButton>
                  </div>
                }
                return (
                  <div
                    key={row.id}
                    data-index={item.index}
                    data-row-id={row.id}
                    data-anchor-row={row.kind === 'block' ? '' : undefined}
                    ref={restoreSettled ? virtualizer.measureElement : undefined}
                    className={cn(row.kind === 'block' && (row.block.kind === 'thinking' || row.block.kind === 'tool' && row.block.name !== 'show_widget') ? 'py-0' : row.kind === 'block' && (row.block.kind === 'tool' || row.block.kind === 'assistant') ? 'py-0.5' : 'py-1.5')}
                  >
                    {row.kind === 'changes' ? <FileChangesCard id={row.id} files={row.files} sessionId={sessionId} turn={row.turn} pending={row.pending} /> : <MessageRow block={row.block} context={context} hideActions={row.block.kind === 'assistant' && row.block.turn !== undefined && turns[row.block.turn]?.reason === 'completed' && turns[row.block.turn]?.endSeq !== undefined && row.block.sourceSeq === turns[row.block.turn]?.finalResponseSeq} />}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {!readOnly && <MessageNavigator key={sessionId} entries={navigationEntries}
        visibleIndex={virtualItems.find(item => item.end > (parentRef.current?.scrollTop ?? 0))?.index ?? 0}
        onJump={entry => { void jumpToMessage(entry) }} />}

      {disconnected ? (
        <div className="pointer-events-none absolute inset-x-0 top-0 z-30 flex justify-center pt-3">
          <div className="pointer-events-auto mx-3 flex flex-wrap items-center justify-center gap-2 rounded-2xl border border-warn/30 bg-warn/10 px-3 py-1 text-2xs text-warn shadow-card backdrop-blur">
            <WifiOff className="h-3 w-3" />
            {connection === 'closed' ? t("ui.components.chat.Conversation.2ab7d89557e7") : t("ui.components.chat.Conversation.e35d503207c5")}
            {onReconnect && <Button size="xs" variant="ghost" onClick={onReconnect}>{t('ui.connection.reconnect')}</Button>}
          </div>
        </div>
      ) : null}

      {!atBottom && hasContentBelow ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-3 left-0 right-0 z-20 flex justify-center">
          <Button
            size="sm"
            variant="secondary"
            onClick={jumpToLatest}
            className="pointer-events-auto shadow-card"
            icon={<ArrowDown className="h-3.5 w-3.5" />}
            iconRight={
              unread > 0 ? (
                <span className="ml-1 rounded-full bg-brand px-1.5 text-2xs text-brand-ink">
                  {unread}
                </span>
              ) : null
            }
          >
            {t("ui.components.chat.Conversation.1c59875d3a15")}
          </Button>
        </div>
      ) : null}

      {running && streaming ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-px overflow-hidden">
          <div className="shimmer-bar h-full w-full bg-transparent" />
        </div>
      ) : null}
    </div>
  )
}

function QueuedEmptyState({ state }: { state: 'paused' | 'environment' | 'queued' }) {
  useLocale()
  return <div role="status" className="mx-auto flex w-full min-w-0 flex-col items-center gap-5 px-6 pb-6 pt-10 text-center" style={{maxWidth: 'var(--chat-content-width)'}}>
    <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand/10 text-brand"><ListOrdered className="h-6 w-6" /></div>
    <h2 className="text-xl font-semibold text-ink">{t('ui.queue.savedHeading')}</h2>
    <p className="max-w-md text-sm leading-6 text-muted">{t(state === 'environment' ? 'ui.queue.environment.noResend' : 'ui.queue.savedDescription')}</p>
    {state === 'paused' && <span className="text-xs text-warn">{t('ui.components.chat.QueueControlDock.d0ca08d60455')}</span>}
  </div>
}

function EmptyState() {
  useLocale()

  return (
    <div
      className="welcome-panel mx-auto flex w-full min-w-0 flex-col items-center gap-6 px-6 pb-8 pt-8 text-center"
      style={{ maxWidth: 'var(--chat-content-width)' }}
    >
      <div className="welcome-mark relative">
        <HarnessLogo className="h-14 w-14" />
      </div>
      <div className="welcome-copy space-y-3.5">
        <h2 className="text-2xl leading-9 font-semibold tracking-tight text-ink">{t("ui.components.chat.Conversation.e33a219fc51f")}</h2>
        <p className="mx-auto max-w-md text-sm leading-relaxed text-muted">{t("ui.components.chat.Conversation.2838a5c07278")}</p>
      </div>
      <ConversationScenarios />
    </div>
  )
}
