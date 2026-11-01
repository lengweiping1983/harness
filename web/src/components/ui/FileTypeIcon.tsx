import { IconBody } from './IconBody'
import { fileType } from '@/lib/fileType'
import { fileIcon } from '@/lib/fileIcon'
import { iconBodies } from './codexIconData'

export function FileTypeIcon({ path, className = 'h-4 w-4 shrink-0' }: { path: string; className?: string }) {
  const { token, color } = fileIcon(path)
  return <span title={token} data-file-type={fileType(path)} data-file-icon={token} className="inline-flex shrink-0" style={{ color }}>
    <svg aria-hidden="true" viewBox="0 0 16 16" className={className}><IconBody>{iconBodies[token] ?? iconBodies.default}</IconBody></svg>
  </span>
}
