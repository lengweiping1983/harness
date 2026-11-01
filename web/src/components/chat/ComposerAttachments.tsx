import type { RefObject } from 'react'
import type { useAttachmentDrafts } from '@/hooks/useAttachmentDrafts'
import { AttachmentDraftTrack } from './AttachmentDraft'

/** 结构化附件上传轨道及文件选择入口。 */
export function ComposerAttachments({attachments, pickerRef}: {
  attachments: ReturnType<typeof useAttachmentDrafts>
  pickerRef: RefObject<HTMLInputElement>
}) {
  return <>
    <AttachmentDraftTrack items={attachments.drafts} onRemove={attachments.remove} />
    <input ref={pickerRef} type="file" multiple className="hidden" onChange={event => {
      void attachments.addFiles(Array.from(event.target.files ?? []))
      event.target.value = ''
    }} />
  </>
}
