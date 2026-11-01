import { describe, expect, it } from 'vitest'
import { listVisibleCount } from '../listPagination'
import { collapseSessions } from '../sidebarGroups'
import { groupedCandidates, type ScoredCandidate } from '../trigger'
import type { SessionSummary } from '../../types'

describe('列表尾数', () => {
  it.each([0, 1, 4, 5, 6])('剩余 %i 项时使用严格少于五项的阈值', remaining => {
    expect(listVisibleCount(10 + remaining, 10)).toBe(remaining < 5 ? 10 + remaining : 10)
  })
  it('侧边栏计入活动会话后不足五项也全部显示', () => {
    const rows = Array.from({ length: 10 }, (_, i) => ({ id: String(i) }) as SessionSummary)
    expect(collapseSessions(rows, true, 5).overflow).toBe(5)
    expect(collapseSessions(rows, true, 5, '9')).toEqual({ shown: rows, overflow: 0 })
  })
  it('引用候选按分类独立吸收尾数，再次展开也适用', () => {
    const rows = ['file', 'skill'].flatMap(kind => Array.from({ length: 14 }, () => ({ candidate: { kind } }) as ScoredCandidate))
    expect(groupedCandidates(rows)).toHaveLength(10)
    expect(groupedCandidates(rows, { file: 10 })).toHaveLength(19)
  })
})
