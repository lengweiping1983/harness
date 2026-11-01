import {afterEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {ConversationCheckout, ConversationCheckoutFields} from '@/components/chat/ConversationCheckout'
import {NewConversationProject} from '@/components/chat/NewConversationProject'
import {useAppStore} from '@/store/app'
import {setLocale, t} from '@/i18n'
import {request} from '../http'
import {noticeText} from '../noticeText'
import {failureText} from '../resourceError'
import {ApiError} from '../http'

// 服务端渲染读取初始快照；此夹具显式用当前目录状态驱动只读呈现。
vi.mock('@/store/app', async original => {
  const module = await original<typeof import('@/store/app')>()
  return {...module, useAppStore: Object.assign((select: (state: ReturnType<typeof module.useAppStore.getState>) => unknown) => select(module.useAppStore.getState()), module.useAppStore)}
})

const options = {cwd: '/project', is_git: true, root: '/project', branch: 'main', head: 'abc', refs: ['main', 'feature', 'origin/remote'], local_branches: ['main', 'feature'], dirty: true}
const value = {cwd: '/project', mode: 'local' as const, ref: 'main'}
afterEach(() => {setLocale('en'); vi.restoreAllMocks(); vi.unstubAllGlobals()})
it('renders local and worktree branches, dirty and first-commit states through the actual shared controls', () => {
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    for (const mode of ['local', 'worktree'] as const) {
      const html = renderToStaticMarkup(<ConversationCheckoutFields options={options} value={{...value, mode}} onChange={() => {}} onRefresh={() => {}} />)
      expect(html).toContain(t(mode === 'local' ? 'ui.newConversation.branch' : 'ui.newConversation.baseBranch'))
      expect(html).toContain(t(mode === 'local' ? 'ui.newConversation.dirtyLocal' : 'ui.newConversation.dirtyIsolated'))
      expect(html.includes('origin/remote')).toBe(mode === 'worktree')
      expect(html).toContain('role="switch"')
    }
    const unborn = renderToStaticMarkup(<ConversationCheckoutFields options={{...options, head: null}} value={value} onChange={() => {}} onRefresh={() => {}} />)
    expect(unborn).toContain(t('ui.newConversation.commitRequired'))
    expect(unborn.match(/<input[^>]*role="switch"[^>]*>/)?.[0]).toContain('disabled=""')
    const folder = renderToStaticMarkup(<ConversationCheckoutFields options={{...options, is_git: false}} value={value} onChange={() => {}} onRefresh={() => {}} />)
    expect(folder).toContain(t('ui.newConversation.folderOnly'))
    expect(folder).not.toContain('role="switch"')
    expect(folder).not.toContain('<select')
    const disabled = renderToStaticMarkup(<ConversationCheckoutFields options={{...options, is_git: null, dirty: null}} value={value} onChange={() => {}} onRefresh={() => {}} />)
    expect(disabled).toContain(t('ui.newConversation.gitDisabled'))
    expect(disabled).not.toContain('role="switch"')
    const loading = renderToStaticMarkup(<ConversationCheckout cwd="/project" value={null} onChange={() => {}} />)
    expect(loading).toContain(t('ui.newConversation.checking'))
  }
})
it('shows project choice before a project exists and preserves the selected project name', () => {
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    useAppStore.setState({activeWorkspaceId: null, workspaces: []})
    expect(renderToStaticMarkup(<NewConversationProject />)).toContain(t('ui.newConversation.chooseProject'))
    useAppStore.setState({activeWorkspaceId: 'project', workspaces: [{id: 'project', name: 'User 原文', path: '/project', color: 'brand'}]})
    expect(renderToStaticMarkup(<NewConversationProject />)).toContain('User 原文')
  }
})
it('rejects damaged workspace option responses and translates accepted error codes without another request', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({...options, dirty: 'false'}), {status: 200}))
    .mockResolvedValueOnce(new Response(JSON.stringify({code: 'session/workspace-dirty', error: 'Original server language'}), {status: 400}))
  vi.stubGlobal('fetch', fetch)
  const malformed = await request('/api/v1/workspace-options?path=%2Fproject')
  expect(malformed).toMatchObject({ok: false, code: 'response/invalid-schema'})
  const rejected = await request('/api/v1/sessions', {method: 'POST'})
  if (rejected.ok) throw Error('Expected refusal')
  const text = failureText(new ApiError(rejected))
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    expect(noticeText(text)).toBe(t('ui.collaboration.error.session/workspace-dirty'))
  }
  expect(fetch).toHaveBeenCalledTimes(2)
})
