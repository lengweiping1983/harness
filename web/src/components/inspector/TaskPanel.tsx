import { changeReviewTurns } from '@/lib/changeReview'
import { t, useLocale } from '@/i18n'
import { useAppStore } from '@/store/app'
import { useChat } from '@/store/chat'
import { useMemo } from 'react'
import { ChangesPanel } from './ChangesPanel'
import { TaskQueue } from './TaskQueue'

export function TaskPanel({ mode, active = true }: { mode: 'queue' | 'changes'; active?: boolean }) {
  useLocale()

  const id = useAppStore((state) => state.activeSessionId)
  const chat = useChat(id)
  const target = useAppStore(state => state.inspectorChangesTarget)
  const reviewTurns = useMemo(() => changeReviewTurns(chat.blocks), [chat.blocks])
  const latestTurn = reviewTurns.find(turn => turn.hasChanges)?.turn
  const historyTarget = useMemo(() => target?.sessionId === id ? target : id && latestTurn ? { sessionId: id, turn: latestTurn, seq: 0 } : null, [target, id, latestTurn])
  if (mode === 'queue') return id ? <TaskQueue key={id} sessionId={id} active={active} /> : <p className="text-xs text-muted">{t("ui.components.inspector.TaskPanel.b21483c44fec")}</p>
  return <ChangesPanel active={active} key={id + ':' + (target?.sessionId === id ? target?.seq : 0)} sessionId={id} historyTarget={historyTarget} historyChoices={reviewTurns} initialHistory={target?.sessionId === id || !!latestTurn} />
}
