import { useState } from 'react'
import { UserRound, Users } from 'lucide-react'

export function AgentAvatar({ src, name, className = 'h-11 w-11', team = false, loading = 'lazy' }: { src?: string | null; name?: string; className?: string; team?: boolean; loading?: 'lazy' | 'eager' }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null)
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null)
  const fallback = !src || failedSrc === src || loadedSrc !== src
  return <span aria-hidden="true" className={`relative inline-flex shrink-0 items-center justify-center overflow-hidden rounded-xl border border-line bg-raised text-sm font-medium text-muted ${className}`}>
    {fallback && (name?.trim() ? <span>{Array.from(name.trim())[0]}</span> : team ? <Users className="h-1/2 w-1/2" /> : <UserRound className="h-1/2 w-1/2" />)}
    {src && failedSrc !== src ? <img key={src} alt="" loading={loading} decoding="async" src={src} className={`absolute inset-0 h-full w-full object-cover ${loadedSrc === src ? 'opacity-100' : 'opacity-0'}`} onLoad={() => setLoadedSrc(src)} onError={() => setFailedSrc(src)} /> : null}
  </span>
}
