import { IconBody } from './IconBody'
import { AlertTriangle, CornerDownRight, Loader2 } from 'lucide-react'
import type { ActivityKind } from '@/lib/toolActivity'
import { iconBodies } from './codexIconData'

export function ToolActivityIcon({ kind, name, running = false, failed = false, skipped = false, className = 'h-4 w-4 shrink-0' }: {
  kind: ActivityKind; name?: string; running?: boolean; failed?: boolean; skipped?: boolean; className?: string
}) {
  if (running) return <Loader2 aria-hidden className={`${className} animate-spin`} />
  if (skipped) return <CornerDownRight aria-hidden className={className} />
  if (failed) return <AlertTriangle aria-hidden className={className} />
  const token = kind === 'read' ? 'command' : kind === 'search' && /^(list_dir|ls|glob)$/.test(name ?? '') ? 'list' : kind
  return <svg aria-hidden="true" data-tool-icon={token} viewBox="0 0 16 16" className={className}><IconBody>{iconBodies[`tool-${token}`] ?? iconBodies['tool-tool']}</IconBody></svg>
}
