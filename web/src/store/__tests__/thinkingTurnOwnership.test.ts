import { describe, expect, it } from 'vitest'
import { displayBlocks, emptySession, reduceEvent, type ChatSessionState } from '../chat'
import { groupToolRuns } from '@/lib/toolRuns'
import type { StreamEvent } from '@/types'

const apply = (state: ChatSessionState, event: Record<string, unknown>) => ({...state, ...reduceEvent(state, event as StreamEvent)})

describe('连续模型请求的思考归属与展示', () => {
  it('十二次无正文思考仍属于当前轮次，汇成一个可展开过程而不污染原文', () => {
    let state = apply(emptySession(), {type: 'turn/start', turn: 1})
    const originals = Array.from({length: 12}, (_,index) => `Original thought ${index} 原文`)
    for (const text of originals) {
      state = apply(state, {type: 'assistant/delta', delta_type: 'thinking', delta: text})
      state = apply(state, {type: 'assistant/message', text: '', thinking: text})
    }
    const blocks = displayBlocks(state)
    expect(blocks.map(block => block.turn)).toEqual(Array(12).fill(1))
    expect(blocks.map(block => 'text' in block ? block.text : null)).toEqual(originals)
    const rows = blocks.map(block => ({kind: 'block' as const, id: block.id, block}))
    const folded = groupToolRuns(rows, {})
    expect(folded).toHaveLength(1)
    expect(folded[0]).toMatchObject({kind: 'tool-run', count: 12, turn: 1, open: false})
    expect(groupToolRuns(rows, {[folded[0]!.id]: true}).slice(1)).toEqual(rows)
  })
  it('最终帧没有先前 delta 时也保留真实当前轮次，未知轮次不补猜', () => {
    const known = apply({...emptySession(), turn: 3}, {type: 'assistant/message', text: '', thinking: 'Original'})
    expect(known.blocks[0]).toMatchObject({kind: 'thinking', turn: 3})
    const unknown = apply(emptySession(), {type: 'assistant/message', text: '', thinking: 'Unknown original'})
    expect(unknown.blocks[0]?.turn).toBeUndefined()
  })
  it('新轮次清除旧草稿身份，流式片段始终归到新的明确轮次', () => {
    let state = apply({...emptySession(), turn: 1}, {type: 'assistant/message', text: '', thinking: 'Old'})
    state = apply(state, {type: 'turn/end', turn: 1, reason: 'completed'})
    state = apply(state, {type: 'turn/start', turn: 2})
    state = apply(state, {type: 'assistant/delta', delta_type: 'thinking', delta: 'New'})
    expect(displayBlocks(state).at(-1)).toMatchObject({kind: 'thinking', text: 'New', turn: 2})
    expect(state.blocks[0]).toMatchObject({kind: 'thinking', text: 'Old', turn: 1})
  })
})
