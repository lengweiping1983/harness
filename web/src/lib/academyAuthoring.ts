import type {AcademyDesign, AcademyReceipt} from '@/types'
import {academyApi, academyReceiptMatches} from './academy'
import type {ApiResult} from './http'
import {createBrowserDraftStorage} from './browserDraftStorage'

export type AcademyTopicDraft = {topic: string; background: string}
export type AcademyDesignPending = AcademyTopicDraft & {requestId: string}
export const academyTopicDraftKey = 'harness.academy.topic.v1'
export const academyDesignPendingKey = 'harness.academy.design.pending.v1'

function topicDraft(value: unknown): value is AcademyTopicDraft {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const row = value as Record<string, unknown>
  return Object.keys(row).every(key => key === 'topic' || key === 'background')
    && typeof row.topic === 'string' && row.topic.length <= 500 && typeof row.background === 'string' && row.background.length <= 2000
}

export function createAcademyTopicDraftStorage(resolve: () => Storage) {
  let failed = false
  let unpersisted = false
  const storage = createBrowserDraftStorage(resolve, () => {failed = true}, raw => {
    try {return topicDraft(JSON.parse(raw))} catch {return false}
  })
  return {
    read() {
      failed = false
      const raw = storage.getItem(academyTopicDraftKey)
      return {draft: raw ? JSON.parse(raw) as AcademyTopicDraft : {topic: '', background: ''}, failed: failed || unpersisted}
    },
    write(draft: AcademyTopicDraft) {
      if (!topicDraft(draft)) return false
      failed = false
      storage.setItem(academyTopicDraftKey, JSON.stringify(draft))
      unpersisted = failed
      return !failed
    },
  }
}

/** 未确认写入只使用实际持久化的请求身份，内存草稿不能代替它。 */
export function readAcademyDesignPending(storage: Pick<Storage, 'getItem'>): AcademyDesignPending | null {
  const raw = storage.getItem(academyDesignPendingKey)
  if (raw === null) return null
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('invalid academy design request')
  const row = value as Record<string, unknown>
  const {requestId, ...draft} = row
  if (typeof requestId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(requestId) || !topicDraft(draft) || !draft.topic.trim()) throw Error('invalid academy design request')
  return {requestId, ...draft}
}

export function academyDesignReceiptMatches(receipt: AcademyReceipt, pending: AcademyDesignPending): boolean {
  if (!academyReceiptMatches(receipt, pending.requestId)) return false
  if (receipt.status !== 'ready') return true
  const design = receipt.result.design
  return !!design && design.topic === pending.topic && design.background === pending.background
}

/** 同一实例串行观察；隐藏和退出同时撤销迟到结果，不取消已经接受的制课。 */
export function watchAcademyDesigns(options: {
  publish: (designs: AcademyDesign[]) => void
  failure: (failure: Exclude<ApiResult<unknown>, {ok: true}>) => void
  read?: typeof academyApi.designs
  interval?: number
  pending?: boolean
}): () => void {
  let disposed = false
  let generating = options.pending ?? false
  let controller: AbortController | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  const refresh = async () => {
    controller = new AbortController()
    const result = await (options.read ?? academyApi.designs)(controller.signal)
    if (disposed) return
    if (result.ok) {
      if (new Set(result.data.designs.map(design => design.id)).size !== result.data.designs.length) {
        options.failure({ok: false, status: 200, code: 'response/invalid-schema', message: '', copy: {key: 'ui.http.invalidResponse'}})
        return
      }
      options.publish(result.data.designs)
      generating = result.data.designs.some(design => design.status === 'generating')
    } else {
      options.failure(result)
      if (result.status >= 400 && result.status < 500 && result.status !== 408 && result.status !== 429) generating = false
    }
    if (generating && !disposed) timer = setTimeout(() => void refresh(), options.interval ?? 2000)
  }
  void refresh()
  return () => {disposed = true; clearTimeout(timer); controller?.abort()}
}
