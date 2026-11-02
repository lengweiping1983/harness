import {afterEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {Inspector} from '@/components/inspector/Inspector'
import {RuntimeTab} from '@/components/inspector/RuntimeTab'
import {TemporaryGrants} from '@/components/inspector/TemporaryGrants'
import {useAppStore} from '@/store/app'
import {setLocale, t} from '@/i18n'
import {INSPECTOR_PANELS} from '../inspectorPanels'

// 静态呈现读取当前快照；Zustand 的服务端默认快照固定为模块初值。
vi.mock('@/store/app', async importOriginal => {
  const actual = await importOriginal<typeof import('@/store/app')>()
  return {...actual, useAppStore: Object.assign(
    (selector: (state: ReturnType<typeof actual.useAppStore.getState>) => unknown) => selector(actual.useAppStore.getState()),
    actual.useAppStore,
  )}
})

const original = useAppStore.getState()
afterEach(() => {useAppStore.setState(original); setLocale('en')})

it('uses Background tasks across navigation, panel titles and authorization guidance', () => {
  useAppStore.setState({activeSessionId: 'background-owner', inspectorTab: 'runtime'})
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const label = language === 'en' ? 'Background tasks' : '后台任务'
    const runtime = INSPECTOR_PANELS.find(panel => panel.value === 'runtime')!
    expect(t(runtime.labelKey)).toBe(label)
    const inspector = renderToStaticMarkup(<Inspector />)
    expect(inspector).toContain(`aria-label="${label}"`)
    expect(inspector).toContain(`class="browser-tab-label">${label}</span>`)
    expect(inspector).toContain(`>${label}</option>`)
    const panel = renderToStaticMarkup(<RuntimeTab />)
    expect(panel).toContain(`<h3 class="ui-section-title">${label}</h3>`)
    expect(panel).toContain(t('ui.runtime.refresh'))
    expect(panel).toContain(t('ui.components.inspector.RuntimeTab.7210d33d82ad'))
    expect(renderToStaticMarkup(<TemporaryGrants sessionId="background-owner" />)).toContain(t('ui.components.inspector.TemporaryGrants.1ff04d1c50ad'))
  }
})
