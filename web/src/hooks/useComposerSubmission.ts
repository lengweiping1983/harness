import { useCallback, useRef, useState } from 'react'
import { useAppStore } from '@/store/app'
import { useChatStore } from '@/store/chat'
import { useDraftStore } from '@/store/drafts'
import { usePlanModeStore } from '@/store/planModes'
import { modeOnlyCommand, modeTaskText, type ComposerMode } from '@/lib/composerMode'
import type { ComposerProps } from '@/components/chat/Composer'
import type { useAttachmentDrafts } from './useAttachmentDrafts'
import type { TeamAddress } from '@/lib/agentMount'

type SubmissionOptions = {
  value: string; draftKey: string; sessionId: string | null
  mode: ComposerMode; hasGoal: boolean; planMode: boolean; teamAddress?: TeamAddress
  disconnected: boolean; canSend: boolean; controlBusy: boolean
  attachments: ReturnType<typeof useAttachmentDrafts>; onSend: ComposerProps['onSend']
  selectMode: (mode: ComposerMode) => void; setValue: (value: string) => void
  setTrigger: (value: null) => void; resize: () => void
  captureFrames: () => (callback: () => void) => void
}

/** 提交准入、单次提交锁与旧会话回包保护。 */
export function useComposerSubmission({ value, draftKey, sessionId,
  mode, hasGoal, planMode, teamAddress, disconnected, canSend, controlBusy,
  attachments, onSend, selectMode, setValue, setTrigger, resize, captureFrames }: SubmissionOptions) {
  const [submitting, setSubmitting] = useState(false)
  const submissionLock = useRef(false)
  const submit = useCallback(async () => {
    const scheduleFrame = captureFrames()
    const onlyMode = modeOnlyCommand(value)
    if (onlyMode) {
      selectMode(onlyMode)
      setValue(''); setTrigger(null); scheduleFrame(resize)
      return
    }
    const text = value.trim()
    if (disconnected || !canSend || controlBusy) return
    if (!text && attachments.readyRefs.length === 0) return
    // 只带走**已上传成功**的引用：失败的条目留在轨道上让用户看到并处理，
    // 而不是让一条注定 404 的引用跟着消息发出去。
    if (submissionLock.current) return
    submissionLock.current = true
    const newSessionRevision = useAppStore.getState().newSessionRevision
    const checkoutRevision = useAppStore.getState().newSessionCheckoutRevision
    const editOrigin = useDraftStore.getState().bySession[draftKey]?.editOrigin
    setSubmitting(true)
    let accepted
    try {
      if (mode !== 'plan' && planMode && sessionId) {
        await usePlanModeStore.getState().change(sessionId, false)
        if (useChatStore.getState().get(sessionId).planMode) return
      }
      if (useAppStore.getState().activeSessionId !== sessionId) return
      accepted = await onSend(modeTaskText(text, mode, hasGoal, planMode), attachments.readyRefs, teamAddress)
    } catch {
      return
    } finally {
      submissionLock.current = false
      setSubmitting(false)
    }
    if (accepted === false) return
    // 旧创建回包不能清除用户再次进入新会话后的草稿。
    if (!sessionId && (useAppStore.getState().newSessionRevision !== newSessionRevision
      || useAppStore.getState().newSessionCheckoutRevision !== checkoutRevision)) return
    const drafts = useDraftStore.getState()
    if (drafts.bySession[draftKey]?.text === value && drafts.bySession[draftKey]?.editOrigin === editOrigin) {
      setValue('')
      drafts.setEditOrigin(draftKey)
    }
    attachments.clearSent()
    if (!sessionId || useAppStore.getState().activeSessionId === sessionId) {
      setTrigger(null)
      scheduleFrame(resize)
    }
  }, [value, mode, hasGoal, planMode, sessionId, selectMode, setValue, teamAddress, draftKey, disconnected, canSend, controlBusy, attachments, onSend, resize, setTrigger, captureFrames])

  return { submit, submitting }
}
