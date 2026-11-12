import type { ResourceText } from '@/types'
import { useLocale } from '@/i18n'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { useEffect, useState } from 'react'
import { loadFileReferences } from '@/lib/fileReferences'

type Loaded = Awaited<ReturnType<typeof loadFileReferences>>

export function useFileReferences(sessionId: string | null, query: string | null, expectedRoot?: string | null) {
  useLocale()
  const key = JSON.stringify([sessionId, query, expectedRoot])
  const [retry, setRetry] = useState({ key: '', count: 0 })
  const [response, setResponse] = useState<{ key: string; value?: Loaded; error?: ResourceText } | null>(null)
  const current = response?.key === key ? response : null
  const enabled = Boolean(sessionId && query !== null)
  useEffect(() => {
    if (!sessionId || query === null) return
    const controller = new AbortController()
    setResponse(null)
    const timer = setTimeout(() => {
      void loadFileReferences(sessionId, query, expectedRoot, controller.signal, retry.key === key).then((value) => {
        if (!controller.signal.aborted) setResponse({ key, value })
      }).catch((error: Error) => { if (!controller.signal.aborted) setResponse({ key, error: failureText(error) }) })
    }, 180)
    return () => { clearTimeout(timer); controller.abort() }
  }, [key, retry.count])
  return { candidates: enabled ? current?.value?.candidates ?? [] : [], result: enabled ? current?.value?.result : undefined,
    loading: enabled && !current, error: enabled && current?.error ? noticeText(current.error) : undefined, retry: () => setRetry((value) => ({ key, count: value.count + 1 })) }
}
