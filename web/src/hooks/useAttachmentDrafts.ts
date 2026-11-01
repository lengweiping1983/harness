import type {ResourceCopy, ResourceText} from '@/types'
import {ApiError} from '@/lib/http'
import {ResourceError, failureText} from '@/lib/resourceError'
import { useCallback, useEffect, useState } from 'react'
import {
  MAX_BATCH_BYTES,
  MAX_BATCH_FILES,
  admitFile,
  bytesToBase64,
  canSendWithDrafts,
  clearDrafts,
  describeBudget,
  dragCarriesFiles,
  filesFromDataTransfer,
  formatSize,
  isPastedImage,
  nameForPastedImage,
  nextLocalId,
  removeDraft,
  type DraftAttachment,
} from '@/lib/attachments'
import { api } from '@/lib/api'
import { useAppStore } from '@/store/app'
import { useDraftStore, EMPTY_ATTACHMENTS } from '@/store/drafts'

function budgetRejection(count: number, bytes: number, name: string, size: number): ResourceCopy | null {
  if (count >= MAX_BATCH_FILES) return {key: 'ui.hooks.useAttachmentDrafts.ed3c41183e18', params: {p0: MAX_BATCH_FILES, p1: name}}
  if (bytes + size > MAX_BATCH_BYTES) return {key: 'ui.hooks.useAttachmentDrafts.ffc628837e25', params: {p0: formatSize(MAX_BATCH_BYTES), p1: name}}
  return null
}

/**
 * 附件草稿轨的状态机。
 *
 * 关键设计：**先入轨、再上传**。用户拖进来的瞬间就有一张卡片出现，
 * 上传在后台跑。另一种常见做法是"等全部上传完再显示卡片"，结果拖一个
 * 20MB 文件时界面毫无反应，用户会以为没拖进来而重复操作。
 *
 * 失败绝不静默：出错的卡片留在轨上并显示原因，用户能看到是**哪个**文件
 * 没进去，也能直接把它删掉然后照发不误。
 */
export interface UseAttachmentDrafts {
  drafts: DraftAttachment[]
  /** 指针正带着文件悬在窗口内（驱动全视口遮罩）。 */
  dragActive: boolean
  /** 当前拖动携带的文件数。 */
  dragCount: number
  ready: boolean
  budget: ReturnType<typeof describeBudget>
  addFiles: (files: File[]) => Promise<void>
  addFromDataTransfer: (transfer: DataTransfer | null) => Promise<void>
  addPasted: (files: File[]) => Promise<boolean>
  remove: (localId: string) => void
  clearSent: () => void
  /** 已上传完成、可以直接发出去的引用。 */
  readyRefs: { attachmentId: string; name: string; mediaType: string; size: number; isImage: boolean }[]
}

export function useAttachmentDrafts(): UseAttachmentDrafts {
  const sessionId = useAppStore((s) => s.activeSessionId) ?? '__unsaved__'
  const drafts = useDraftStore((s) => s.bySession[sessionId]?.attachments ?? EMPTY_ATTACHMENTS)
  const setDrafts = useCallback((update: (current: DraftAttachment[]) => DraftAttachment[]) => {
    const store = useDraftStore.getState()
    if (store.bySession[sessionId]) store.updateAttachments(sessionId, update)
  }, [sessionId])
  // 跨窗口拖放深度与文件数：遮罩要在指针扫过整个窗口时保持稳定，
  // 子元素冒泡不能让它闪一下就消失。
  const [dragDepth, setDragDepth] = useState(0)
  const [dragCount, setDragCount] = useState(0)
  const toast = useAppStore((s) => s.toast)
  // 草稿属于任务而非组件；切换任务后上传仍更新原任务。

  const uploadOne = useCallback(async (localId: string, file: File, name: string, mediaType: string) => {
    setDrafts((current) =>
      current.map((item) => (item.localId === localId ? { ...item, status: 'uploading', error: undefined } : item)),
    )
    try {
      const bytes = new Uint8Array(await file.arrayBuffer())
      if (!useDraftStore.getState().bySession[sessionId]?.attachments.some(item => item.localId === localId)) return
      const result = await api.uploadAttachments([
        { name, mediaType, data: bytesToBase64(bytes) },
      ])
      const info = result.ok ? result.data.attachments[0] : null
      if (!info) {
        if (!result.ok) throw new ApiError(result)
        throw new ResourceError({key: "ui.hooks.useAttachmentDrafts.2c89db12669c"})
      }
      setDrafts((current) =>
        current.map((item) =>
          item.localId === localId
            ? (() => { if (item.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(item.previewUrl); return { ...item, status: undefined, error: undefined, attachmentId: info.attachmentId, previewUrl: item.isImage ? api.attachmentUrl(info.attachmentId) : undefined } })()
            : item,
        ),
      )
    } catch (error) {
      const message = error instanceof Error ? failureText(error) : {key: "ui.hooks.useAttachmentDrafts.219481a6dde7"}
      setDrafts((current) =>
        current.map((item) =>
          item.localId === localId ? { ...item, status: 'error', error: message } : item,
        ),
      )
    }
  }, [sessionId, setDrafts])

  const addFiles = useCallback(
    async (incoming: File[], pasted = false) => {
      if (incoming.length === 0) return
      const revision = useAppStore.getState().newSessionRevision
      const destinationExists = () => sessionId === '__unsaved__'
        ? useAppStore.getState().newSessionRevision === revision
        : useAppStore.getState().sessions.some(session => session.id === sessionId)
      if (!destinationExists()) return
      const rejections: ResourceText[] = []
      const accepted: { localId: string; file: File; name: string; mediaType: string; size: number; isImage: boolean }[] = []

      // 逐个准入，并**同时**核对批量限额。限额基于本轨道已有内容累加：
      // 一次拖 100 个文件时，超限的那些逐条告知，而不是整批失败 ——
      // 用户宁可少发几个，也不想重新挑一遍。
      // A screenshot can finish after the user changed chats or sent this draft.
      // Read the original destination's current budget rather than a stale render.
      const currentDrafts = useDraftStore.getState().bySession[sessionId]?.attachments ?? EMPTY_ATTACHMENTS
      let usedBytes = currentDrafts.reduce((sum, item) => sum + item.size, 0)
      let usedCount = currentDrafts.length
      let counter = currentDrafts.length
      for (const raw of incoming) {
        const name = pasted && isPastedImage(raw) ? nameForPastedImage(raw, ++counter) : raw.name
        const size = raw.size
        const budgetError = budgetRejection(usedCount, usedBytes, name, size)
        if (budgetError) {
          rejections.push(budgetError)
          continue
        }
        // 只读头部做魔数判断：整文件读进来只为了 sniff 是纯浪费。
        let head: Uint8Array
        try { head = new Uint8Array(await raw.slice(0, 64).arrayBuffer()) }
        catch {
          if (!destinationExists()) return
          rejections.push({template: {key: 'ui.attachments.fileRejected', params: {name, error: {key: 'ui.attachments.readFailed'}}}})
          continue
        }
        if (!destinationExists()) return
        const verdict = admitFile(name, head, size)
        if (!verdict.ok) {
          rejections.push({template: {key: 'ui.attachments.fileRejected', params: {name, error: verdict.message ?? {key: 'ui.hooks.useAttachmentDrafts.bdb662e5bb55'}}}})
          continue
        }
        usedBytes += size
        usedCount += 1
        accepted.push({
          localId: nextLocalId(),
          file: raw,
          name,
          mediaType: verdict.mediaType!,
          size,
          isImage: Boolean(verdict.isImage),
        })
      }

      const admitted: typeof accepted = []
      // 准入头部读取会让出调度；最终预算与入轨必须在同一次同步更新中完成。
      if (accepted.length > 0) useDraftStore.getState().updateAttachments(sessionId, current => {
        let bytes = current.reduce((sum, item) => sum + item.size, 0)
        const next = [...current]
        for (const entry of accepted) {
          const budgetError = budgetRejection(next.length, bytes, entry.name, entry.size)
          if (budgetError) rejections.push(budgetError)
          else {
            bytes += entry.size
            admitted.push(entry)
            next.push({localId: entry.localId, name: entry.name, mediaType: entry.mediaType,
              size: entry.size, isImage: entry.isImage, status: 'pending',
              previewUrl: entry.isImage ? URL.createObjectURL(entry.file) : undefined})
          }
        }
        return next
      })
      if (rejections.length > 0) {
        const details = rejections.slice(0, 3).reduce<ResourceText>((first, second) => first ? {template: {key: 'ui.common.feedbackPair', params: {first, second}}} : second, '')
        toast({
          title: rejections.length === 1 ? {key: "ui.hooks.useAttachmentDrafts.5b72886ba0b9"} : {key: "ui.hooks.useAttachmentDrafts.ee043c4a0f9b", params: {p0: rejections.length}},
          description: rejections.length > 3 ? {template: {key: 'ui.attachments.rejectionsTruncated', params: {items: details}}} : details,
          tone: 'warn',
        })
      }
      // 上传不 await：卡片已经出现在界面上了，之后是后台进度。
      for (const entry of admitted) {
        void uploadOne(entry.localId, entry.file, entry.name, entry.mediaType)
      }
    },
    [sessionId, toast, uploadOne],
  )

  const addFromDataTransfer = useCallback(
    async (transfer: DataTransfer | null) => {
      await addFiles(filesFromDataTransfer(transfer))
    },
    [addFiles],
  )

  const addPasted = useCallback(
    async (files: File[]) => {
      // 只接管图片文件：普通文本粘贴必须继续走原路径，否则用户粘贴一段
      // 代码或路径会被静默吞掉。返回是否被接管，供调用方决定要不要
      // 阻止默认粘贴行为。
      const shots = files.filter((file) => file.type.startsWith('image/'))
      if (shots.length === 0) return false
      await addFiles(shots, true)
      return true
    },
    [addFiles],
  )

  /**
   * 窗口级拖放接管。
   *
   * 为什么必须挂在 window 上：拖放遮罩是**全视口**的，它写着一句"松开以添加 N
   * 个附件"。如果 drop 只挂在输入区那个页脚 div 上，用户把文件拖到对话记录
   * 上方（那才是他视线落点、也是屏幕面积最大的地方）松手，遮罩消失、什么都没
   * 发生 —— 提示与行为不一致比没有提示更糟。所以 enter/over/leave 用来驱动遮罩，
   * drop 也在 window 上接住，从任何位置松手都算数。
   *
   * 只在确实带着文件时才干预；拖选文本、拖链接等一律放行。
   */
  const useWindowDrop = useCallback((): (() => void) => {
    const carriesFiles = (event: DragEvent): boolean => dragCarriesFiles(event.dataTransfer?.types)

    const onDragEnter = (event: DragEvent): void => {
      if (!carriesFiles(event)) return
      event.preventDefault()
      setDragDepth((depth) => depth + 1)
      setDragCount(event.dataTransfer?.items?.length ?? 0)
    }
    const onDragOver = (event: DragEvent): void => {
      if (!carriesFiles(event)) return
      // 不 preventDefault 浏览器就不会派发 drop —— 这是 drop 失效的头号原因。
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
    }
    const onDragLeave = (event: DragEvent): void => {
      if (!carriesFiles(event)) return
      setDragDepth((depth) => {
        const next = Math.max(0, depth - 1)
        if (next === 0) setDragCount(0)
        return next
      })
    }
    const onDrop = (event: DragEvent): void => {
      if (!carriesFiles(event)) return
      // 必须阻止默认：否则浏览器会用被拖入的文件**导航**掉整个 SPA，
      // 用户辛苦攒的会话就没了。
      event.preventDefault()
      setDragDepth(0)
      setDragCount(0)
      void addFromDataTransfer(event.dataTransfer)
    }

    window.addEventListener('dragenter', onDragEnter)
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('dragleave', onDragLeave)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('dragenter', onDragEnter)
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('dragleave', onDragLeave)
      window.removeEventListener('drop', onDrop)
    }
  }, [addFromDataTransfer])

  // 直接返回清理函数：effect 的返回值就是卸载时执行的解绑。
  useEffect(() => useWindowDrop(), [useWindowDrop])

  const remove = useCallback((localId: string) => {
    setDrafts((current) => removeDraft(current, localId))
  }, [setDrafts])

  const clearSent = useCallback(() => {
    const ids = new Set(drafts.filter(item => item.attachmentId && !item.status).map(item => item.localId))
    setDrafts(current => {
      clearDrafts(current.filter(item => ids.has(item.localId)))
      return current.filter(item => !ids.has(item.localId))
    })
  }, [drafts, setDrafts])

  const readyRefs = drafts
    .filter((item) => Boolean(item.attachmentId) && !item.status)
    .map((item) => ({
      attachmentId: item.attachmentId!,
      name: item.name,
      mediaType: item.mediaType,
      size: item.size,
      isImage: item.isImage,
    }))

  return {
    drafts,
    dragActive: dragDepth > 0,
    dragCount,
    ready: canSendWithDrafts(drafts),
    budget: describeBudget(drafts),
    addFiles,
    addFromDataTransfer,
    addPasted,
    remove,
    clearSent,
    readyRefs,
  }
}
