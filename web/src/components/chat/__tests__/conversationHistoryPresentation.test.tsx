import {afterEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {Conversation} from '../Conversation'
import {setLocale, t} from '@/i18n'
import type {LoadedHistory} from '@/store/chat'

// 只替换依赖浏览器几何的虚拟化，历史控制及消息使用生产组件。
vi.mock('@tanstack/react-virtual', () => ({useVirtualizer: ({count}: {count: number}) => ({
  getVirtualItems: () => Array.from({length: count}, (_, index) => ({index, start: index * 100, end: (index + 1) * 100})),
  getTotalSize: () => count * 100,
})}))
// 静态呈现不执行布局，不用服务端渲染器模拟客户端布局效果。
vi.mock('react', async original => {
  const react = await original<typeof import('react')>()
  return {...react, useLayoutEffect: react.useEffect}
})

const history: LoadedHistory = {through: 11, start: 0, end: 11, total: 11, has_more: false, next_cursor: null,
  user_messages: [], cursorThrough: 11, generation: 1, revision: 0, loading: false, error: null, resetRequired: false, ids: ['request']}
const props = {blocks: [{kind: 'user' as const, id: 'request', at: 1, text: '开发一款 3D CS 游戏，需要有真实感'}],
  running: false, streaming: false, connection: 'open'}

afterEach(() => setLocale('zh-CN'))

it('进入已完整加载的对话不显示历史计数，也不保留占位行', () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    for (const value of [null, history, {...history, total: 0, end: 0}]) {
      const html = renderToStaticMarkup(<Conversation {...props} history={value} />)
      expect(html).not.toContain('data-row-id="conversation-history"')
      expect(html).not.toContain(t('ui.conversation.historyCount', {loaded: 11, total: 11}))
      expect(html).toContain(props.blocks[0].text)
      expect(html).toContain('data-user-message-body=""')
    }
  }
})

it('部分历史保留显示更多、读取中和错误重试入口，但不展示事件计数', () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    const partial = {...history, has_more: true, end: 5}
    for (const value of [partial, {...partial, loading: true}, {...partial, error: {key: 'ui.http.invalidResponse'}}]) {
      const html = renderToStaticMarkup(<Conversation {...props} history={value} />)
      expect(html).toContain('data-row-id="conversation-history"')
      expect(html).not.toContain(t('ui.conversation.historyCount', {loaded: 5, total: 11}))
      expect(html).toContain(t(value.loading ? 'ui.components.chat.Conversation.3e8f9af3b2e4'
        : value.error ? 'ui.components.chat.Conversation.6d7f1def5670' : 'ui.showMore'))
      if (value.loading) expect(html).toContain('disabled=""')
      if (value.error) expect(html).toContain(t('ui.http.invalidResponse'))
    }
  }
})

it('没有更多历史时仍呈现读取错误及重新读取入口', () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    const html = renderToStaticMarkup(<Conversation {...props} history={{...history, error: {key: 'ui.http.invalidResponse'}, resetRequired: true}} />)
    expect(html).toContain('role="alert"')
    expect(html).toContain(t('ui.http.invalidResponse'))
    expect(html).toContain(t('ui.components.chat.Conversation.3014a4a1a292'))
  }
})

it('零条历史读取中的状态仍可见，读取失败也不退回空白', () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    const empty = {...history, total: 0, end: 0, ids: []}
    const loading = renderToStaticMarkup(<Conversation {...props} blocks={[]} history={{...empty, loading: true}} />)
    expect(loading).toContain(`<span role="status" aria-live="polite">${t('ui.components.chat.Conversation.bca2a7dd358e')}</span>`)
    const failed = renderToStaticMarkup(<Conversation {...props} blocks={[]} history={{...empty, error: {key: 'ui.http.invalidResponse'}, resetRequired: true}} />)
    expect(failed).toContain(t('ui.http.invalidResponse'))
    expect(failed).toContain(t('ui.components.chat.Conversation.3014a4a1a292'))
  }
})
