import { afterEach, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { setLocale } from '@/i18n'
import { useAppStore } from '../app'

const pending = <T,>() => {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

afterEach(() => { vi.restoreAllMocks(); setLocale('en') })
it('refreshes model prose and rejects stale catalogs even after returning to the same language', async () => {
  setLocale('en')
  useAppStore.setState({ languageSaving: false, models: [{ id: 'm', provider: 'fixture', label: 'Original', active: true, visible: false }], tools: [], skills: [] })
  const reads = [0, 1, 2].map(() => ({
    tools: pending<Awaited<ReturnType<typeof api.tools>>>(),
    skills: pending<Awaited<ReturnType<typeof api.skills>>>(),
    models: pending<Awaited<ReturnType<typeof api.models>>>(),
  }))
  vi.spyOn(api, 'tools').mockImplementationOnce(() => reads[0]!.tools.promise).mockImplementationOnce(() => reads[1]!.tools.promise).mockImplementationOnce(() => reads[2]!.tools.promise)
  vi.spyOn(api, 'skills').mockImplementationOnce(() => reads[0]!.skills.promise).mockImplementationOnce(() => reads[1]!.skills.promise).mockImplementationOnce(() => reads[2]!.skills.promise)
  vi.spyOn(api, 'models').mockImplementationOnce(() => reads[0]!.models.promise).mockImplementationOnce(() => reads[1]!.models.promise).mockImplementationOnce(() => reads[2]!.models.promise)
  vi.spyOn(api, 'updateSettings').mockImplementation(async ({ language }) => ({ ok: true, data: { settings: { language, effective_language: language } } }) as Awaited<ReturnType<typeof api.updateSettings>>)
  for (const locale of ['zh-CN', 'en', 'zh-CN'] as const) await useAppStore.getState().setLanguage(locale)
  const finish = async (index: number, prose: string) => {
    const read = reads[index]!
    read.tools.resolve({ ok: true, data: { tools: [{ name: 'tool', description: prose }] } })
    read.skills.resolve({ ok: true, data: { skills: [{ name: 'skill', description: prose }] } })
    read.models.resolve({ ok: true, data: { models: [{ id: 'm', provider: 'fixture', label: prose, description: prose, active: false, visible: true }], active: 'other', providers: [] } })
    await Promise.all([read.tools.promise, read.skills.promise, read.models.promise])
  }
  await finish(2, '当前中文')
  expect(useAppStore.getState().models[0]).toEqual({ id: 'm', provider: 'fixture', label: '当前中文', description: '当前中文', active: true, visible: false })
  await finish(0, '过期中文')
  await finish(1, 'Stale English')
  expect(useAppStore.getState().tools[0]?.description).toBe('当前中文')
  expect(useAppStore.getState().skills[0]?.description).toBe('当前中文')
  expect(useAppStore.getState().models[0]?.description).toBe('当前中文')
})
