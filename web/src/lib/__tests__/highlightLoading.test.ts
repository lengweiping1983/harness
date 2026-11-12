import {afterEach, expect, it, vi} from 'vitest'

const state = vi.hoisted(() => ({
  core: vi.fn(), language: vi.fn(), registered: new Set<string>(), register: vi.fn(),
}))
vi.mock('highlight.js/lib/core', () => state.core())
vi.mock('highlight.js/lib/languages/javascript', () => state.language())
afterEach(() => {vi.resetModules(); vi.clearAllMocks(); state.registered.clear()})

it('核心和语言并行下载，别名与并发代码块只注册一次', async () => {
  let release!: () => void
  const barrier = new Promise<void>(resolve => {release = resolve})
  const core = {getLanguage: (name: string) => state.registered.has(name), registerLanguage: (name: string) => {
    state.register(name); state.registered.add(name)
  }}
  state.core.mockImplementation(async () => {await barrier; return {default: core}})
  state.language.mockResolvedValue({default: () => ({})})
  const {loadLanguage} = await import('../highlight')
  const requests = [loadLanguage('js'), loadLanguage('javascript'), loadLanguage('jsx')]
  await vi.waitFor(() => {expect(state.core).toHaveBeenCalledOnce(); expect(state.language).toHaveBeenCalledOnce()})
  expect(state.register).not.toHaveBeenCalled()
  release()
  expect(await Promise.all(requests)).toEqual([core, core, core])
  expect(state.register).toHaveBeenCalledExactlyOnceWith('javascript')
  expect(await loadLanguage('mjs')).toBe(core)
  expect(state.register).toHaveBeenCalledOnce()
})

it('未知语言不下载核心，也不把对象原型当语言', async () => {
  const {loadLanguage, isHighlightable} = await import('../highlight')
  for (const language of ['plaintext', 'unknown', 'constructor', '__proto__']) {
    expect(isHighlightable(language)).toBe(false)
    expect(await loadLanguage(language)).toBeNull()
  }
  expect(state.core).not.toHaveBeenCalled()
  expect(state.language).not.toHaveBeenCalled()
})
