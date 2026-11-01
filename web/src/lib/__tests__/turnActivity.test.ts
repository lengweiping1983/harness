import { describe, expect, it } from 'vitest'
import { conversationRows, mergeTurnActivity, turnElapsed } from '../turnActivity'
import type { Block } from '@/types'
import type { ChangeRow } from '../fileChanges'

const rows = (blocks: Block[]): ChangeRow[] => blocks.map(block => ({kind: 'block', id: block.id, block}))
const user: Block = {kind: 'user', id: 'user', turn: 1, sourceSeq: 1, at: 1000, text: 'Original'}
const thought: Block = {kind: 'thinking', id: 'thought', turn: 1, at: 2000, text: 'Original thought'}
const running: Block = {kind: 'tool', id: 'tool', turn: 1, at: 3000, name: 'execute', callId: 'call', status: 'running'}
const start = {turn: 1, startedAt: 1000, startSeq: 0}

describe('turn elapsed and disclosure projection', () => {
  it('shows one timer before the first process, including waiting and direct answers', () => {
    for (const blocks of [[user], [user, thought], [user, {kind: 'assistant', id: 'reply', turn: 1, at: 2000, text: 'Answer'} as Block]]) {
      const result = conversationRows(rows(blocks), {1: start}, {})
      expect(result[0]).toMatchObject({id: 'user'})
      expect(result[1]).toMatchObject({kind: 'activity', id: 'activity-turn-1'})
      expect(result.filter(row => row.kind === 'activity')).toHaveLength(1)
    }
  })
  it('keeps running tools, failures, questions and final replies visible while the process is folded', () => {
    const question: Block = {kind: 'question', id: 'question', turn: 1, at: 3000, questionId: 'q', questions: [], answered: false}
    const answer: Block = {kind: 'assistant', id: 'answer', turn: 1, at: 4000, sourceSeq: 9, text: 'Final'}
    const result = conversationRows(rows([user, thought, running, {...running, id: 'failed', status: 'error'}, question, answer]),
      {1: {...start, endedAt: 5000, endSeq: 10, reason: 'completed', finalResponseSeq: 9}}, {1: false})
    expect(result.map(row => row.id)).toEqual(['user', 'activity-turn-1', 'tool', 'failed', 'question', 'answer'])
    expect(result[1]).toMatchObject({count: 1, open: false, partial: false})
  })
  it('uses immutable event boundaries after refresh and never extends a completed turn', () => {
    const turns = mergeTurnActivity({}, [
      {type: 'turn/start', seq: 0, time: 1000, data: {turn: 1}},
      {type: 'turn/end', seq: 10, time: 178000, data: {turn: 1, reason: {kind: 'completed'}, final_response_seq: 9}},
      {type: 'turn/start', seq: 11, time: 179000, data: {turn: 2}},
    ])
    expect(turnElapsed(turns[1]!, 999999)).toBe(177000)
    expect(turnElapsed(turns[2]!, 180000)).toBe(1000)
    expect(turnElapsed(turns[2]!)).toBeNull()
    const partial = conversationRows(rows([thought]), turns, {})
    expect(partial[0]).toMatchObject({kind: 'activity', partial: true})
  })
  it('does not invent durations for missing, invalid or reversed timestamps', () => {
    for (const activity of [{turn: 1}, {...start, startedAt: NaN}, {...start, endedAt: Infinity}, {...start, endedAt: 999}]) {
      expect(turnElapsed(activity)).toBeNull()
    }
    expect(turnElapsed({...start, endedAt: 1000})).toBe(0)
  })
})

it.each(['pending','running','completed','failed','interrupted','done','error','COMPLETED'])('folds only current completed Agent activity: %s', status => {
  const agent:Block={kind:'subagent',id:'agent',turn:1,at:2000,agentId:'agent',description:'Original 原文',status}
  const result=conversationRows(rows([user,agent]),{1:{...start,endedAt:3000,endSeq:3,reason:'completed'}},{1:false})
  expect(result.some(row=>row.id==='agent')).toBe(status!=='completed')
  expect(result.find(row=>row.kind==='activity')).toMatchObject({count:status==='completed'?1:0})
})
