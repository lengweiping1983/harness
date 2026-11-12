import {useEffect, useState} from 'react'
import {api} from '@/lib/api'
import {usePageVisible} from './usePageVisible'

/** 只读核对服务所在机器的 CPU；未确认前不挂载录音或提供语音导航。 */
export function useVoiceInputSupported(active = true): boolean {
  const visible = usePageVisible()
  const [supported, setSupported] = useState(false)
  useEffect(() => {
    if (!active || !visible) return
    const controller = new AbortController()
    void api.voiceDictation(controller.signal).then(result => {
      if (!controller.signal.aborted) setSupported(result.ok && result.data.cpu_supported)
    }).catch(() => {if (!controller.signal.aborted) setSupported(false)})
    return () => controller.abort()
  }, [active, visible])
  return active && supported
}
