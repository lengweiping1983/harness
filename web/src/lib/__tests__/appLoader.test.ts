import {expect, it, vi} from 'vitest'

const fixture = vi.hoisted(() => {
  let release!: () => void
  const app = new Promise<void>(resolve => {release = resolve})
  const state = {booted: false, booting: false, boot: vi.fn(async () => {state.booting = true})}
  return {app, release, state}
})
vi.mock('../../App', async () => {await fixture.app; return {default: () => null}})
vi.mock('../../store/app', () => ({useAppStore: {getState: () => fixture.state}}))

it('starts boot once while the application module is still downloading', async () => {
  const {loadApp} = await import('../appLoader')
  const first = loadApp()
  expect(loadApp()).toBe(first)
  await vi.waitFor(() => expect(fixture.state.boot).toHaveBeenCalledTimes(1))
  let rendered = false
  void first.then(() => {rendered = true})
  expect(rendered).toBe(false)
  fixture.release()
  expect((await first).default).toBeTypeOf('function')
  expect(fixture.state.boot).toHaveBeenCalledTimes(1)
})
