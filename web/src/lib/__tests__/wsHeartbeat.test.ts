import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SessionSocket } from '../ws'

class FakeSocket {
  static OPEN = 1
  static CONNECTING = 0
  static instances: FakeSocket[] = []
  readyState = 0
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn()
  constructor(public url: string) { FakeSocket.instances.push(this) }
  open() { this.readyState = 1; this.onopen?.() }
  message(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }) }
}

function connection() {
  const onState = vi.fn(), onEvent = vi.fn()
  const client = new SessionSocket({ sessionId: 's', getSince: () => 7, onState, onEvent })
  client.connect()
  const socket = FakeSocket.instances[0]
  socket.open()
  return { client, socket, onState, onEvent }
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.instances = []
  vi.stubGlobal('window', { location: { protocol: 'http:', host: 'localhost' } })
  vi.stubGlobal('WebSocket', FakeSocket)
})
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('WebSocket heartbeat recovery', () => {
  it('reconnects after missing pong without waiting for a close event', () => {
    const { client, socket, onState } = connection()
    vi.advanceTimersByTime(20_000)
    expect(JSON.parse(socket.send.mock.calls[0][0])).toMatchObject({ action: 'ping' })
    vi.advanceTimersByTime(10_000)
    expect(socket.close).toHaveBeenCalledWith(4000, 'heartbeat-timeout')
    expect(onState.mock.calls.at(-1)?.[0]).toBe('reconnecting')
    vi.advanceTimersByTime(1000)
    expect(FakeSocket.instances).toHaveLength(2)
    expect(FakeSocket.instances[1].url).toContain('since=7')
    // 失效连接的迟到事件不能让新连接再次重连或交付事件。
    socket.onclose?.()
    socket.message({ type: 'pong' })
    expect(FakeSocket.instances).toHaveLength(2)
    client.close()
  })

  it('accepts pong as transport control without forwarding it to business events', () => {
    const { client, socket, onEvent } = connection()
    vi.advanceTimersByTime(20_000)
    socket.message({ type: 'pong' })
    vi.advanceTimersByTime(10_000)
    expect(socket.close).not.toHaveBeenCalled()
    expect(onEvent).not.toHaveBeenCalled()
    socket.message({ type: 'turn/start' })
    expect(onEvent).toHaveBeenCalledWith({ type: 'turn/start' })
    expect(FakeSocket.instances).toHaveLength(1)
    client.close()
  })

  it('ignores valid JSON frames that are not event objects', () => {
    const { client, socket, onEvent } = connection()
    for (const payload of [null, [], 1, true, 'text']) socket.message(payload)
    expect(onEvent).not.toHaveBeenCalled()
    socket.message({ type: 'turn/start' })
    expect(onEvent).toHaveBeenCalledOnce()
    expect(onEvent).toHaveBeenCalledWith({ type: 'turn/start' })
    client.close()
  })

  it('reconnects when a heartbeat cannot be sent', () => {
    const { client, socket, onState } = connection()
    socket.send.mockImplementation(() => { throw new Error('broken transport') })
    client.ping()
    expect(socket.close).toHaveBeenCalledWith(4000, 'heartbeat-timeout')
    expect(onState.mock.calls.at(-1)?.[0]).toBe('reconnecting')
    client.close()
  })

  it('cancels a pending heartbeat deadline when explicitly closed', () => {
    const { client, socket, onState } = connection()
    vi.advanceTimersByTime(20_000)
    client.close()
    vi.advanceTimersByTime(60_000)
    expect(socket.close).toHaveBeenCalledTimes(1)
    expect(FakeSocket.instances).toHaveLength(1)
    expect(onState.mock.calls.at(-1)?.[0]).toBe('closed')
  })
})
