import {t, useLocale} from '@/i18n'
import {useAppStore} from '@/store/app'
import {resolveLink} from '@/lib/fileLinks'
import {FileTypeIcon} from '@/components/ui/FileTypeIcon'

/* 工具：给"纯文本"场景用的内联文件链接（如工具卡片里的路径）。 */
export function FileChip({ path, label }: { path: string; label?: string }) {
  useLocale()

  const reveal = useAppStore((s) => s.revealInFiles)
  const resolved = resolveLink(path)
  if (resolved.kind === 'unsafe') return (
    <span className="inline-flex max-w-full items-center gap-1 rounded-md border border-line bg-raised px-1.5 py-0.5 text-xs text-muted" title={t('ui.files.outsideWorkspace')}>
      <FileTypeIcon path={path} />
      <span className="truncate">{label ?? path}</span>
    </span>
  )
  return (
    <a
      href={resolved.href}
      download={resolved.download || undefined}
      onClick={resolved.workspacePath && !resolved.download ? (event) => { event.preventDefault(); reveal(resolved.workspacePath!, resolved.lineRange, resolved.workspaceRoot === undefined ? undefined : { root: resolved.workspaceRoot }) } : undefined}
      target="_blank"
      rel="noopener noreferrer"
      className="bg-raised text-muted hover:text-ink inline-flex max-w-full items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-xs transition-colors"
      title={path}
    >
      <FileTypeIcon path={path} />
      <span className="truncate">{label ?? path.split(/[\\/]/).pop() ?? path}</span>
    </a>
  )
}
