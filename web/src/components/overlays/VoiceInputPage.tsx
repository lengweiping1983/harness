import {useEffect, useRef, useState} from 'react'
import {Mic} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {api} from '@/lib/api'
import {isUnconfirmedWrite, type ApiFailure} from '@/lib/http'
import {noticeText} from '@/lib/noticeText'
import {usePageVisible} from '@/hooks/usePageVisible'
import {Button, RefreshButton} from '@/components/ui/primitives'
import type {ResourceText} from '@/types'
import type {VoiceDictationCapabilities} from '@/lib/protocol.generated'

function failureCopy(result: ApiFailure): ResourceText {
  if (result.code === 'voice/local-only') return {key: 'ui.voice.localOnly'}
  if (result.code === 'voice/unavailable') return {key: 'ui.voice.dictation.launchFailed'}
  return result.copy ?? result.message
}

export function VoiceInputPage() {
  useLocale()
  const visible = usePageVisible()
  const [capabilities, setCapabilities] = useState<VoiceDictationCapabilities | null>(null)
  const [reading, setReading] = useState(false)
  const [opening, setOpening] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [notice, setNotice] = useState<ResourceText>('')
  const generation = useRef(0)
  const working = useRef(false)
  const reader = useRef<AbortController | null>(null)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    return () => {mounted.current = false; generation.current++; reader.current?.abort()}
  }, [])

  async function refresh() {
    if (working.current || document.hidden) return
    const current = ++generation.current
    reader.current?.abort()
    const controller = new AbortController()
    reader.current = controller
    setReading(true)
    const result = await api.voiceDictation(controller.signal)
    if (!mounted.current || current !== generation.current || controller.signal.aborted) return
    if (result.ok) {setCapabilities(result.data); setError('')}
    else {setCapabilities(null); setError(failureCopy(result))}
    setReading(false)
  }

  useEffect(() => {
    if (visible) void refresh()
    return () => {generation.current++; reader.current?.abort()}
  }, [visible])

  async function open() {
    if (working.current || !capabilities?.available || document.hidden) return
    working.current = true
    reader.current?.abort()
    const current = ++generation.current
    setReading(false); setOpening(true); setError(''); setNotice('')
    try {
      const result = await api.openVoiceDictation()
      if (!mounted.current || current !== generation.current) return
      if (result.ok) setNotice({key: 'ui.voice.dictation.requested'})
      else {
        // 打开回执不明时只回查安装状态，不能据此补发打开请求或宣称听写就绪。
        setError(isUnconfirmedWrite(result) ? {key: 'ui.voice.dictation.unconfirmed'} : failureCopy(result))
        const controller = new AbortController()
        reader.current = controller
        const status = await api.voiceDictation(controller.signal)
        if (!mounted.current || current !== generation.current) return
        if (status.ok) setCapabilities(status.data)
      }
    } finally {
      working.current = false
      if (mounted.current) setOpening(false)
    }
  }

  const unavailable = capabilities && !capabilities.available
  return <section className="space-y-5">
    <div>
      <h3 className="ui-section-title">{t('ui.voice.dictation.title')}</h3>
      <p className="mt-2 text-sm leading-relaxed text-muted">{t('ui.voice.dictation.description')}</p>
    </div>
    <div className="space-y-4 rounded-xl border border-line p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h4 className="ui-card-title">{t('ui.voice.dictation.otherApps')}</h4>
        <RefreshButton size="sm" label={t('ui.voice.dictation.refresh')} spinning={reading} disabled={reading || opening} onClick={() => void refresh()} />
      </div>
      <p className="text-xs leading-relaxed text-muted">{t('ui.voice.dictation.setup')}</p>
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="primary" icon={<Mic aria-hidden className="h-4 w-4" />} disabled={!capabilities?.available || reading || opening}
          loading={opening} onClick={open}>{t('ui.voice.dictation.open')}</Button>
        {unavailable && capabilities.download_url && <a href={capabilities.download_url} target="_blank" rel="noreferrer" className="text-xs text-brand underline">{t('ui.voice.dictation.install')}</a>}
      </div>
      {reading && <p role="status" className="text-xs text-muted">{t('ui.voice.dictation.checking')}</p>}
      {unavailable && capabilities.reason && <p role="status" className="break-words text-xs text-warn">{noticeText(capabilities.reason)}</p>}
      {error && <p role="alert" className="break-words text-xs text-danger">{noticeText(error)}</p>}
      {notice && <p role="status" className="text-xs text-muted">{noticeText(notice)}</p>}
      {capabilities?.available && <p className="text-xs leading-relaxed text-muted">{t(capabilities.platform === 'macos' ? 'ui.voice.dictation.useMac' : 'ui.voice.dictation.useWindows')}</p>}
      <p className="text-xs leading-relaxed text-faint">{t('ui.voice.dictation.lifecycle')}</p>
    </div>
    <div className="space-y-2">
      <h4 className="ui-card-title">{t('ui.voice.dictation.inHarness')}</h4>
      <p className="text-xs leading-relaxed text-muted">{t('ui.voice.dictation.composer')}</p>
    </div>
  </section>
}
