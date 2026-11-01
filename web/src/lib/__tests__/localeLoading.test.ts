import {afterEach, expect, it, vi} from 'vitest'
const english = vi.hoisted(() => vi.fn())
vi.mock('@/i18n/locales/en.json', () => english())
afterEach(() => {vi.resetModules(); vi.unstubAllEnvs(); vi.useRealTimers(); english.mockReset()})

it('生产首屏只加载当前语言，切换等待资源，迟到旧选择不覆盖新选择', async () => {
  vi.resetModules()
  let finish!: (module: unknown) => void
  english.mockImplementation(() => new Promise(resolve => {finish = resolve}))
  const {getLocale, setLocale, t} = await import('@/i18n')
  expect(getLocale()).toBe('zh-CN')
  expect(english).not.toHaveBeenCalled()
  const change = setLocale('en')
  await vi.waitFor(() => expect(english).toHaveBeenCalledOnce())
  expect(getLocale()).toBe('zh-CN')
  await setLocale('zh-CN')
  finish({default: {'ui.sidebar.loading': 'Loading conversations…'}})
  await change
  expect(getLocale()).toBe('zh-CN')
  await setLocale('en')
  expect(t('ui.sidebar.loading')).toBe('Loading conversations…')
  expect(english).toHaveBeenCalledOnce()
})
