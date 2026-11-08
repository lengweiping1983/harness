import type { ConnectionState, StreamEvent } from '@/types'

/**
 * 会话 WebSocket 客户端。
 *
 * 三件事让它在真实网络下稳：
 *
 * 1. **自动重连 + 指数退避** —— 0.5s 起，翻倍，封顶 15s，加抖动，
 *    避免后端重启时几十个标签页同时砸过来。
 * 2. **seq 续传** —— 记住最后收到的 seq，重连时带 `?since=`，
 *    服务端只补发缺口，前端不会因为断线丢一段对话。
 * 3. **应用层心跳** —— 浏览器 WS 只有协议 ping 不可见，而模型首个 token
 *    可能几十秒；20s 一次 `{"action":"ping"}` 顺带探测半开连接。
 */

export interface SessionSocketOptions {
  sessionId: string
  onEvent: (event: StreamEvent) => void
  onState: (state: ConnectionState, detail?: { attempt: number; nextRetryMs?: number }) => void
  /** 最近一次已消费的 seq，用于断线补发。 */
  getSince?: () => number
  /** Choose the persisted conversation window needed for UI restoration. */
  snapshotView?: 'conversation' | 'conversation-window'
}

const PING_INTERVAL = 20_000
const PONG_TIMEOUT = 10_000
const BASE_DELAY = 500
const MAX_DELAY = 15_000
const MAX_ATTEMPTS = 40

function jitter(base: number): number {
  return Math.round(base * (0.75 + Math.random() * 0.5))
}

export function wsUrl(sessionId: string, since: number, view?: 'conversation' | 'conversation-window'): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const base = window.location.host
  return protocol + '//' + base + '/ws?session=' + encodeURIComponent(sessionId) + '&since=' + since + (view ? '&view=' + view : '')
}

export class SessionSocket {
  private ws: WebSocket | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private pongTimer: ReturnType<typeof setTimeout> | null = null
  private attempt = 0
  private disposed = false
  private opts: SessionSocketOptions

  constructor(options: SessionSocketOptions) {
    this.opts = options
  }

  get connected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }

  connect(): void {
    if (this.disposed) return
    this.clearTimers()
    if (this.attempt === 0) this.opts.onState('connecting', { attempt: 0 })

    let socket: WebSocket
    try {
      socket = new WebSocket(wsUrl(this.opts.sessionId, this.opts.getSince?.() ?? 0, this.opts.snapshotView))
    } catch {
      this.scheduleReconnect()
      return
    }
    this.ws = socket

    socket.onopen = () => {
      if (this.ws !== socket || this.disposed) return
      this.attempt = 0
      this.opts.onState('open')
      this.pingTimer = setInterval(() => this.ping(), PING_INTERVAL)
    }

    socket.onmessage = (message) => {
      if (this.ws !== socket || this.disposed) return
      if (typeof message.data !== 'string') return
      let decoded: unknown
      try {
        decoded = JSON.parse(message.data)
      } catch {
        return
      }
      if (typeof decoded !== 'object' || decoded === null || Array.isArray(decoded)) return
      const payload = decoded as StreamEvent
      if (payload.type === 'pong') {
        if (this.pongTimer !== null) {
          clearTimeout(this.pongTimer)
          this.pongTimer = null
        }
        return
      }
      this.opts.onEvent(payload)
    }

    socket.onerror = () => {
      // onerror 之后浏览器必定触发 onclose，这里不重复处理。
    }

    socket.onclose = () => {
      if (this.ws !== socket) return
      this.clearTimers()
      this.ws = null
      if (this.disposed) {
        this.opts.onState('closed')
        return
      }
      this.scheduleReconnect()
    }
  }

  close(): void {
    this.disposed = true
    this.clearTimers()
    const socket = this.ws
    this.ws = null
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      try {
        socket.close(1000, 'client')
      } catch {
        /* 关闭失败无需处理 */
      }
    }
    this.opts.onState('closed')
  }

  /** 显式重置退避（例如用户点了"重连"）。 */
  reset(): void {
    this.close()
    this.attempt = 0
    this.disposed = false
    this.clearTimers()
    this.connect()
  }

  send(payload: Record<string, unknown>): boolean {
    if (this.ws === null || this.ws.readyState !== WebSocket.OPEN) return false
    try {
      this.ws.send(JSON.stringify(payload))
      return true
    } catch {
      return false
    }
  }

  ping(): void {
    if (this.pongTimer !== null || !this.connected) return
    const socket = this.ws
    const recover = () => {
      if (this.ws !== socket || this.disposed) return
      this.clearTimers()
      this.ws = null
      // 不等待半开连接的关闭握手；旧连接的迟到回调由身份检查丢弃。
      try { socket?.close(4000, 'heartbeat-timeout') } catch { /* 继续重建连接。 */ }
      this.scheduleReconnect()
    }
    if (!this.send({ action: 'ping', t: Date.now() })) { recover(); return }
    this.pongTimer = setTimeout(recover, PONG_TIMEOUT)
  }

  private scheduleReconnect(): void {
    if (this.attempt >= MAX_ATTEMPTS) {
      this.opts.onState('closed')
      return
    }
    this.attempt += 1
    const delay = Math.min(BASE_DELAY * Math.pow(2, this.attempt - 1), MAX_DELAY)
    const wait = jitter(delay)
    this.opts.onState('reconnecting', { attempt: this.attempt, nextRetryMs: wait })
    this.timer = setTimeout(() => this.connect(), wait)
  }

  private clearTimers(): void {
    if (this.pongTimer !== null) {
      clearTimeout(this.pongTimer)
      this.pongTimer = null
    }
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }
}