import {ResourceError, failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import { ComposerRemoveButton } from './ComposerRemoveButton'
import { useState } from 'react'
import { ContextActionMenu, type ContextMenuAnchor } from '@/components/ui/ContextActionMenu'
import { copyImage, saveImageAs } from '@/lib/imageActions'
import { t, useLocale } from '@/i18n'
import { AlertTriangle, Copy, Download, FileCode2, FileText, Loader2, Paperclip } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatSize, isPreviewable, type DraftAttachment } from '@/lib/attachments'
import { useAppStore } from '@/store/app'

/**
 * 附件草稿轨。
 *
 * 设计取舍（这些都是"人类习惯"的具体落点）：
 *
 *  - **横向卡片条，不是图标网格**。网格在超过 6 个附件后会把输入框撑得很高，
 *    而输入框的高度是固定的；横条只占一行，超出时横向滚动。
 *  - **图片直接出缩略图，且可点击放大预览**。对齐全局 ImageLightbox，右上角关闭、
 *    支持多图切页、滚轮缩放与 Esc 键关闭。
 *  - **逐个显示状态**。上传中是一个转圈的小圆点，失败是一枚警示图标加一行原因；
 *    把失败的项留在轨道上并可移除，比整批静默丢弃有用得多 —— 用户能看到
 *    到底是哪一个文件没进去。
 *  - **移除按钮统一显现**：始终显示，不依赖悬停发现入口。
 */

export interface AttachmentDraftTrackProps {
  items: DraftAttachment[]
  onRemove: (localId: string) => void
  onPreview?: (item: DraftAttachment) => void
}

export function AttachmentDraftTrack({ items, onRemove, onPreview }: AttachmentDraftTrackProps) {
  useLocale()

  const openGallery = useAppStore((s) => s.openImageGallery)
  const openImage = useAppStore((s) => s.openLightbox)

  if (items.length === 0) return null

  const imageItems = items.filter((i) => isPreviewable(i.mediaType) && Boolean(i.previewUrl))
  const gallery = imageItems.map((i) => ({ src: i.previewUrl!, alt: i.name }))

  const handlePreview = (item: DraftAttachment) => {
    if (onPreview) {
      onPreview(item)
      return
    }
    if (!item.previewUrl) return
    const idx = imageItems.findIndex((i) => i.localId === item.localId)
    if (gallery.length > 1 && idx !== -1) {
      openGallery(gallery, idx)
    } else {
      openImage(item.previewUrl, item.name)
    }
  }

  return (
    <div className="px-2.5 pb-1 pt-2">
      <div
        className="flex gap-1.5 overflow-x-auto pb-0.5"
        role="list"
        aria-label={t("ui.components.chat.AttachmentDraft.d630b6745fc5")}
      >
        {items.map((item) => (
          <DraftChip
            key={item.localId}
            item={item}
            onRemove={onRemove}
            onPreview={isPreviewable(item.mediaType) && item.previewUrl ? () => handlePreview(item) : undefined}
          />
        ))}
      </div>
    </div>
  )
}

function DraftChip({
  item,
  onRemove,
  onPreview,
}: {
  item: DraftAttachment
  onRemove: (localId: string) => void
  onPreview?: () => void
}) {
  useLocale()

  const [anchor, setAnchor] = useState<ContextMenuAnchor | null>(null)
  const showThumb = isPreviewable(item.mediaType) && item.previewUrl
  async function act(action: 'copy' | 'save') {
    if (!item.previewUrl) return
    try {
      if (action === 'copy') await copyImage(item.previewUrl)
      else await saveImageAs(item.previewUrl, item.name)
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      useAppStore.getState().toast({ title: error instanceof ResourceError ? failureText(error) : {key: action === 'copy' ? 'ui.imageActions.copyFailed' : 'ui.imageActions.saveFailed'}, tone: 'danger' })
    }
  }
  return (<>
    <div
      tabIndex={showThumb ? 0 : undefined}
      aria-haspopup={showThumb ? 'menu' : undefined}
      onContextMenu={event => {
        if (!showThumb) return
        event.preventDefault(); event.stopPropagation()
        setAnchor({ id: item.localId, x: event.clientX, y: event.clientY, origin: event.currentTarget })
      }}
      onKeyDown={event => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
        if (!showThumb || !(event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))) return
        event.preventDefault(); event.stopPropagation()
        const rect = event.currentTarget.getBoundingClientRect()
        setAnchor({ id: item.localId, x: rect.left, y: rect.bottom, origin: event.currentTarget })
      }}
      role="listitem"
      title={item.error ? noticeText(item.error) : `${item.name} · ${formatSize(item.size)}`}
      className={cn(
        'group/composer-item relative flex min-h-14 w-auto min-w-48 max-w-xs shrink-0 items-center gap-2 overflow-hidden rounded-lg border bg-raised/70 px-1.5 py-1',
        item.status === 'error' ? 'border-danger/40' : 'border-line',
      )}
    >
      {showThumb ? (
        <button
          type="button"
          onClick={onPreview}
          title={t("ui.components.markdown.Markdown.fea4c16d2986")}
          aria-label={t("ui.components.markdown.Markdown.fea4c16d2986")}
          className="group/thumb relative h-11 w-11 shrink-0 overflow-hidden rounded-md cursor-zoom-in focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand"
        >
          <img
            src={item.previewUrl}
            alt={item.name}
            className="h-full w-full object-cover transition-transform duration-150 group-hover/thumb:scale-105"
            draggable={false}
          />
        </button>
      ) : (
        <span
          className={cn(
            'flex h-11 w-11 shrink-0 items-center justify-center rounded-md border border-line bg-canvas/60',
            item.status === 'error' && 'text-danger',
          )}
        >
          {item.status === 'error' ? (
            <AlertTriangle className="h-4 w-4" />
          ) : (
            <FileIcon mediaType={item.mediaType} />
          )}
        </span>
      )}

      <span className="min-w-0 flex-1 pr-6">
        <span className="line-clamp-2 break-all text-xs font-medium text-ink">{item.name}</span>
        <span className="mt-0.5 flex items-center gap-1 text-2xs text-faint">
          {item.status === 'uploading' ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin" />{t("ui.components.chat.AttachmentDraft.403b055e56f5")}</>
          ) : item.status === 'error' ? (
            <span className="truncate text-danger">{t("ui.components.chat.AttachmentDraft.f098218a3032")}</span>
          ) : (
            <>
              <span className="truncate">{shortType(item.mediaType)}</span>
              <span aria-hidden>·</span>
              <span className="shrink-0">{formatSize(item.size)}</span>
            </>
          )}
        </span>
      </span>

      <ComposerRemoveButton
        className="h-6 w-6 opacity-100 pointer-events-auto"
        onClick={() => onRemove(item.localId)}
        aria-label={t("ui.components.chat.AttachmentDraft.715b458f46f0", {p0: (item.name)})}
      />
    </div>
    {anchor && showThumb ? <ContextActionMenu anchor={anchor} label={item.name} onClose={() => setAnchor(null)} items={[
      { id: 'copy', label: t('ui.imageActions.copy'), icon: <Copy aria-hidden className="h-4 w-4" />, run: () => void act('copy') },
      { id: 'save', label: t('ui.imageActions.save'), icon: <Download aria-hidden className="h-4 w-4" />, run: () => void act('save') },
    ]} /> : null}
  </>)
}

function FileIcon({ mediaType }: { mediaType: string }) {
  useLocale()

  if (mediaType.startsWith('image/')) return <Paperclip className="h-4 w-4" />
  if (mediaType === 'application/pdf') return <FileText className="h-4 w-4" />
  return <FileCode2 className="h-4 w-4" />
}

/** 媒体类型压成短标签，避免在窄卡片上写不下完整类型串。 */
function shortType(mediaType: string): string {
  const subtype = mediaType.split('/')[1] ?? ''
  const table: Record<string, string> = {
    'markdown': 'MD',
    'plain': t("ui.components.chat.AttachmentDraft.dd81861811e5"),
    'csv': 'CSV',
    'json': 'JSON',
    'jsonl': 'JSONL',
    'xml': 'XML',
    'x-yaml': 'YAML',
    'yaml': 'YAML',
    'pdf': 'PDF',
  }
  return Object.hasOwn(table, subtype) ? table[subtype]! : subtype.toUpperCase()
}

/**
 * 全视口拖放遮罩。
 *
 * 拖动文件经过窗口时，浏览器只在 dragover 期间提供事件，而用户在拖动
 * 过程中**看不到自己拖的是什么** —— 只有指针下一个模糊的轮廓。所以必须
 * 在 dragenter 之后给出一个覆盖全窗口的高对比提示，否则用户会以为没反应，
 * 反复拖第二次。松开就消失，且用 `pointer-events-none` 保证不会挡住 drop 事件。
 */
export function DropOverlay({ active, count }: { active: boolean; count: number }) {
  useLocale()

  if (!active) return null
  return (
    <div
      className="pointer-events-none fixed inset-0 z-modal flex items-center justify-center bg-canvas/70 backdrop-blur-scrim"
      aria-hidden
    >
      <div className="flex flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-brand/60 bg-raised/90 px-10 py-7 shadow-2xl">
        <Paperclip className="h-7 w-7 text-brand" />
        <p className="text-sm font-medium text-ink">
          {count > 0 ? t("ui.components.chat.AttachmentDraft.34e1fa56efc0", {p0: (count)}) : t("ui.components.chat.AttachmentDraft.7395955bcf0f")}
        </p>
        <p className="text-xs text-muted">{t("ui.components.chat.AttachmentDraft.6ebabcd8b88c")}</p>
      </div>
    </div>
  )
}
