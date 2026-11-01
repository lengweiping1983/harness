import {useDraftStore} from '@/store/drafts'
import {useQueueEditStore} from '@/store/queueEdits'
import {gitDraftBelongsToSession, useGitDraftStore} from '@/store/gitDrafts'

/** 只捕获原对话的本地内容；不可用其他对话的写入阻止原删除清理。 */
export function captureSessionLocalDrafts(sessionId: string) {
  const drafts = useDraftStore.getState(), queue = useQueueEditStore.getState()
  return {
    draft: drafts.bySession[sessionId],
    submissions: drafts.submissions[sessionId],
    queueEdit: queue.bySession[sessionId],
    withdrawal: queue.withdrawals[sessionId],
    git: Object.fromEntries(Object.entries(useGitDraftStore.getState().byScope).filter(([scope]) => gitDraftBelongsToSession(scope, sessionId))),
  }
}

/** 状态仓库不可变更新；保留等待期间替换的任何原对话草稿。 */
export function sameSessionLocalDrafts(before: ReturnType<typeof captureSessionLocalDrafts>, after: ReturnType<typeof captureSessionLocalDrafts>): boolean {
  return before.draft === after.draft && before.submissions === after.submissions &&
    before.queueEdit === after.queueEdit && before.withdrawal === after.withdrawal &&
    Object.keys(before.git).length === Object.keys(after.git).length &&
    Object.entries(before.git).every(([scope, message]) => after.git[scope] === message)
}
