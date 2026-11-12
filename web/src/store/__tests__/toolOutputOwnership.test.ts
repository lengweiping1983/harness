import { describe, expect, it } from 'vitest'
import { emptySession, reduceEvent, type ChatSessionState } from '../chat'
import type { StreamEvent, ToolBlockState } from '@/types'

const output = (turn: number, stdout = 'late') => ({ type: 'tool/output', call_id: 'same', turn, output_seq: 1, stdout, stderr: '', stdout_offset: 0, stderr_offset: 0 }) as StreamEvent
const tool = (id: string, turn: number, status: ToolBlockState['status'] = 'running'): ToolBlockState => ({ id, turn, kind: 'tool', at: 0, callId: 'same', name: 'shell', status })
const apply = (state: ChatSessionState, event: StreamEvent) => ({ ...state, ...reduceEvent(state, event) })

describe('工具输出的轮次归属', () => {
  it('旧轮次迟到输出不污染复用 ID 的新调用', () => {
    const state = { ...emptySession(), turn: 2, blocks: [tool('old', 1, 'ok'), tool('new', 2)] }
    const next = apply(state, output(1))
    expect(next.blocks).toEqual(state.blocks)
    const current = apply(next, output(2, 'current'))
    expect((current.blocks[1] as ToolBlockState).progress?.stdout).toBe('current')
    expect((current.blocks[0] as ToolBlockState).progress).toBeUndefined()
  })

  it('没有配对开始帧的旧输出不创建幽灵运行卡片', () => {
    const state = { ...emptySession(), turn: 2 }
    expect(apply(state, output(1)).blocks).toEqual([])
    expect(apply(state, output(3)).blocks).toEqual([])
  })

  it('当前孤立输出保留轮次，开始帧重放不重复创建', () => {
    let state = apply({ ...emptySession(), turn: 2 }, output(2, 'first'))
    expect(state.blocks).toHaveLength(1)
    expect(state.blocks[0]?.turn).toBe(2)
    state = apply(state, { type: 'tool/start', call_id: 'same', turn: 2, name: 'shell' } as StreamEvent)
    expect(state.blocks).toHaveLength(1)
    state = apply(state, { type: 'tool/end', call_id: 'same', turn: 2, content: 'done', is_error: false } as StreamEvent)
    expect((state.blocks[0] as ToolBlockState).status).toBe('ok')
    expect((state.blocks[0] as ToolBlockState).output).toBe('done')
  })
})
