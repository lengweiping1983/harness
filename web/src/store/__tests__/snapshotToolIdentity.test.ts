import { noticeText } from '@/lib/noticeText'
import { describe, expect, it } from 'vitest'
import { blocksFromDetail } from '../chat'
import type { SessionDetail, ToolBlockState } from '@/types'

const snapshot = (windowed: boolean): SessionDetail => ({
  header: { id: 'fixture' }, usage: {}, todos: [], stats: {}, timeline: [],
  ...(windowed ? { events_view: 'conversation-window' as const } : {}),
  messages: [1, 2, 3].flatMap(turn => [
    { kind: 'message', role: 'assistant' as const, turn, source_seq: turn * 10 + 1,
      tool_uses: [{ id: 'same', name: `tool${turn}`, input: { path: `file${turn}` } }] },
    ...(turn < 3 ? [{ kind: 'message', role: 'tool' as const, turn, source_seq: turn * 10 + 5,
      results: [{ tool_use_id: 'same', content: `result${turn}`, is_error: false }] }] : []),
  ]),
  events: [1, 2, 3].flatMap(turn => [
    { type: 'turn/start', seq: turn * 10, data: { turn } },
    { type: 'tool/call', seq: turn * 10 + 2, data: { call_id: 'same', turn, status: 'running', name: `tool${turn}` } },
    { type: 'note', seq: turn * 10 + 3, data: { kind: 'permission_decision', call_id: 'same', decision: ['allow'], reason: `reason${turn}` } },
    { type: 'tool/output', seq: turn * 10 + 4, data: { call_id: 'same', turn, output_seq: 1, stdout: `output${turn}`, stderr: '', stdout_offset: 0, stderr_offset: 0 } },
  ]),
})

describe('快照工具调用轮次身份', () => {
  it.each([false, true])('恢复各轮输入、进度与当前运行卡片，windowed=%s', windowed => {
    const detail = snapshot(windowed)
    const blocks = blocksFromDetail(detail)
    const notices = blocks.filter(block => block.kind === 'notice')
    expect(notices).toHaveLength(2)
    expect(noticeText(notices[0]!.text)).toContain('reason1')
    expect(noticeText(notices[1]!.text)).toContain('reason2')
    const tools = blocks.filter((block): block is ToolBlockState => block.kind === 'tool')
    expect(tools).toHaveLength(3)
    expect(tools.map(tool => tool.turn)).toEqual([1, 2, 3])
    expect(tools.map(tool => tool.input)).toEqual([{ path: 'file1' }, { path: 'file2' }, { path: 'file3' }])
    expect(tools.map(tool => tool.progress?.stdout)).toEqual(['output1', 'output2', 'output3'])
    expect(tools.map(tool => tool.status)).toEqual(['ok', 'ok', 'running'])
  })

  it.each([false, true])('当前快照字段不被旧别名覆盖，windowed=%s', windowed => {
    const detail = snapshot(windowed)
    for (const message of detail.messages) {
      if (message.role === 'assistant') message.tool_calls = [{ id: 'same', name: 'obsolete', input: { path: 'obsolete' } }]
    }
    const tools = blocksFromDetail(detail).filter((block): block is ToolBlockState => block.kind === 'tool')
    expect(tools.map(tool => tool.input)).toEqual([{ path: 'file1' }, { path: 'file2' }, { path: 'file3' }])
  })

  it('后续助手调用不能回填到更早的工具结果', () => {
    const detail = snapshot(false)
    detail.messages = [
      { kind: 'message', role: 'tool', turn: 1, source_seq: 1, results: [{ tool_use_id: 'same', content: 'orphan' }] },
      { kind: 'message', role: 'assistant', turn: 1, source_seq: 2, tool_uses: [{ id: 'same', name: 'future', input: { secret: 'later' } }] },
    ]
    detail.events = []
    const tool = blocksFromDetail(detail).find(block => block.kind === 'tool') as ToolBlockState
    expect(tool.input).toBeUndefined()
    expect(tool.name).toBe('tool')
  })
})
