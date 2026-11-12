import { t, useLocale } from '@/i18n'
import { memo, useState } from 'react'
import { ChevronRight, Download, FileText, PackageCheck } from 'lucide-react'
import type { DeliverableFile, DeliverablesBlock as DeliverablesBlockModel } from '@/types'
import { cn } from '@/lib/utils'
import { getFileRoot } from '@/lib/fileRoot'
import { fileUrl, isImagePath } from '@/lib/fileLinks'
import { groupDeliverables, workspaceRelativePath, summarizeDeliverables } from '@/lib/deliverables'
import { useAppStore } from '@/store/app'
import { Badge } from '@/components/ui/primitives'

/**
 * 回合结束时的「这一轮改了什么」卡片。
 *
 * 设计取舍：
 *  - 默认展示清单，可收起；刷新后从已保存的成功工具结果恢复。
 *  - 每行两个动作：文件名在右侧预览，旁边的下载按钮获取文件。
 *  - 图片行给缩略图。产物类回合里图占大头，纯文字列表会让人多切一次 Tab。
 */

const FILE_ICON_TONE = 'text-faint'

function sizeLabel(bytes: number | undefined): string | null {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n <= 0) return null
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function DeliverableRow({ file, root }: { file: DeliverableFile; root: string | null }) {
  useLocale()

  const reveal = useAppStore((s) => s.revealInFiles)
  const relative = workspaceRelativePath(file.path, root)
  if (relative === null) return <li className="px-1.5 py-1 text-xs text-muted">
    <p className="break-all">{file.path}</p>
    <p>{t('ui.files.outsideWorkspace')}</p>
  </li>
  const href = fileUrl(relative, { root })
  const size = sizeLabel(file.size_bytes)
  const preview = isImagePath(relative) ? href : null

  return (
    <li className="group/deliverable flex items-start gap-2 rounded-md px-1.5 py-1 transition-colors hover:bg-raised/70">
      {preview ? (
        <img
          src={preview}
          alt=""
          loading="lazy"
          className="mt-px h-7 w-7 shrink-0 rounded border border-line object-cover"
        />
      ) : (
        <FileText className={cn('mt-px h-3.5 w-3.5 shrink-0', FILE_ICON_TONE)} />
      )}
      <button
        type="button"
        onClick={() => reveal(relative, undefined, { root })}
        title={t("ui.components.chat.blocks.DeliverablesCard.e05526d303ac", {p0: (relative)})}
        className="min-w-0 flex-1 text-left"
      >
        <span className="block truncate text-xs font-medium text-ink" title={relative}>
          {relative || file.name}
        </span>
        {file.description ? (
          <span className="mt-0.5 block truncate text-2xs text-muted" title={file.description}>
            {file.description}
          </span>
        ) : null}
      </button>
      {size ? <span className="shrink-0 pt-px tabular-nums text-2xs text-faint">{size}</span> : null}
      {/* 定位到右侧面板是主路径；直链下载是"我就要这个文件"的旁路。 */}
      <a
        href={fileUrl(relative, { root, download: true })}
        download
        title={t("ui.components.chat.blocks.DeliverablesCard.6eef973edda4", {p0: (file.name)})}
        aria-label={t("ui.components.chat.blocks.DeliverablesCard.6eef973edda4", {p0: (file.name)})}
        className="shrink-0 rounded p-1 text-muted transition-colors hover:bg-raised hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
      >
        <Download className="h-3.5 w-3.5" />
      </a>
    </li>
  )
}

export const DeliverablesCard = memo(function DeliverablesCard({
  block,
}: {
  block: DeliverablesBlockModel
}) {
  useLocale()

  const [open, setOpen] = useState(true)
  const root = block.workspaceRoot === undefined ? getFileRoot() : block.workspaceRoot
  const groups = groupDeliverables(block.files, root)

  if (!block.files.length) return null

  return (
    <section
      className="animate-slide-up overflow-hidden rounded-lg border border-brand/25 bg-brand/[0.04]"
      aria-label={t("ui.components.chat.blocks.DeliverablesCard.2a255c8d5964")}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ChevronRight
          className={cn(
            'h-3.5 w-3.5 shrink-0 text-brand transition-transform duration-200',
            open && 'rotate-90',
          )}
        />
        <PackageCheck className="h-3.5 w-3.5 shrink-0 text-brand" />
        <span className="text-xs font-semibold text-ink">{t("ui.components.chat.blocks.DeliverablesCard.1840e5fe199d")}</span>
        <Badge tone="brand">{summarizeDeliverables(block.files)}</Badge>
        {groups.length > 1 ? (
          <span className="text-2xs text-faint">{t('ui.deliverables.directoryCount', {count: groups.length})}</span>
        ) : null}
        <span className="ml-auto text-2xs text-faint">{t("ui.components.chat.blocks.DeliverablesCard.4141e90710fe")}</span>
      </button>
      {open ? (
        <div className="space-y-2 border-t border-brand/15 px-2 py-2">
          {groups.map((group) => (
            <div key={group.dir || '__root__'}>
              {groups.length > 1 ? (
                <p className="px-1.5 pb-0.5 text-2xs font-medium text-muted">{group.label}/</p>
              ) : null}
              <ul>
                {group.files.map((file) => (
                  <DeliverableRow key={file.path} file={file} root={root} />
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  )
})
