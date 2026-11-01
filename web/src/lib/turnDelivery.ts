import type {TurnArtifact} from './protocol.generated'

/** 仅使用原始回执声明的目录；缺失或越界时不借当前项目补齐。 */
export function artifactPreviewTarget(artifact: TurnArtifact): {path: string; root: string} | null {
  if (typeof artifact.workspace_root !== 'string' || !artifact.workspace_root.trim()) return null
  let root = artifact.workspace_root.replace(/\\/g, '/').replace(/\/+$/, '') || '/'
  if (/^[a-z]:$/i.test(root)) root += '/'
  const path = artifact.path.replace(/\\/g, '/')
  if (!/^(?:[a-z]:\/|\/)/i.test(root) || /[\0]/.test(root + path) || root.split('/').some(part => part === '.' || part === '..')) return null
  const prefix = root.endsWith('/') ? root : root + '/'
  const windows = /^[a-z]:\//i.test(root) || root.startsWith('//')
  if (!(windows ? path.toLowerCase().startsWith(prefix.toLowerCase()) : path.startsWith(prefix))) return null
  const relative = path.slice(prefix.length)
  if (!relative || relative.split('/').some(part => !part || part === '.' || part === '..')) return null
  return {path: relative, root: artifact.workspace_root}
}
