import type {ToolBlockState} from '@/types'

export function authoredPlugin(block: ToolBlockState): {path: string; name: string} | null {
  if (block.name !== 'author_plugin' || block.status !== 'ok' || block.isError || block.outcomePending) return null
  const value = block.display
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const data = value as Record<string, unknown>
  if (data.kind !== 'plugin_source' || typeof data.name !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(data.name)
    || data.name.length > 64 || typeof data.path !== 'string' || data.path.includes('\0')
    || !/^(?:\/|[a-zA-Z]:[\\/]|\\\\)/.test(data.path)) return null
  return {path: data.path, name: data.name}
}
