import type { DeliverablesBlock } from '@/types'

/** Only a successful present result is durable evidence of an artifact. */
export function presentedArtifacts(name: string, failed: boolean, display: unknown,
  id: string, at: number, sourceSeq?: number): DeliverablesBlock | null {
  if (name !== 'present' || failed || !display || typeof display !== 'object') return null
  const data = display as Record<string, unknown>
  if (!Array.isArray(data.files)) return null
  const files = data.files.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const file = item as Record<string, unknown>
    if (typeof file.path !== 'string' || !file.path.trim()) return []
    return [{ path: file.path, name: typeof file.name === 'string' ? file.name : file.path.split(/[\\/]/).pop()!,
      description: typeof file.description === 'string' ? file.description : '',
      size_bytes: typeof file.size_bytes === 'number' && Number.isFinite(file.size_bytes) && file.size_bytes >= 0 ? file.size_bytes : undefined }]
  })
  if (!files.length) return null
  return { id: `${id}:artifacts`, kind: 'deliverables', at, sourceSeq, files,
    turn: typeof data.turn === 'number' ? data.turn : undefined,
    ...(Object.hasOwn(data, 'workspace_root') && (data.workspace_root === null || typeof data.workspace_root === 'string')
      ? { workspaceRoot: data.workspace_root as string | null } : {}) }
}
