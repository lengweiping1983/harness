import {describe, expect, it} from 'vitest'
import {responseIssue} from '../responseValidation'
import type {MemoryPreview} from '../protocol.generated'

const path = '/api/v1/sessions/fixture/memory/preview'
const report: MemoryPreview = {session_id: 'fixture', source_seq: null, budget: 8000, used_chars: 0,
  layers: [{scope: 'project', status: 'ready', budget: 4000, error_code: '', recall: {
    budget: 4000, used_chars: 0, total: 1, selected: [], suppressed: [],
    skipped_budget: [{id: 'large', revision: 2, reason: 'pinned', source_status: 'untracked', derivation_status: 'none'}], skipped_query_limit: 0, unmatched: 0,
  }}]}

describe('记忆召回预览公共契约', () => {
  it('接受具有条目身份、版本及预算解释的预览', () => {
    expect(responseIssue(path, 'GET', 200, report)).toBeFalsy()
  })
  it('拒绝不明状态和缺少条目修订的成功响应', () => {
    expect(responseIssue(path, 'GET', 200, {...report, layers: [{...report.layers[0], status: 'unknown'}]})).toBeTruthy()
    expect(responseIssue(path, 'GET', 200, {...report, layers: [{...report.layers[0], recall: {
      ...report.layers[0].recall, selected: [{id: 'entry', reason: 'query'}],
    }}]})).toBeTruthy()
  })
})
