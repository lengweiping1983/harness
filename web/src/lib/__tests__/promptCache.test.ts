import {describe, expect, it} from 'vitest'
import {cacheMetrics} from '../promptCache'

describe('provider cache evidence', () => {
  it('uses reported input when positive cache reads carry no coverage field', () => {
    const usage = {input_tokens: 101521, output_tokens: 21501, cache_read_tokens: 89088}
    expect(cacheMetrics(usage)).toMatchObject({invalid: false, reported: 101521, coverage: 1, hitRate: 89088 / 101521})
    expect(usage).not.toHaveProperty('cache_reported_input_tokens')
  })
  it('keeps partial coverage, explicit zero hits and unknown reports distinct', () => {
    expect(cacheMetrics({input_tokens: 100, cache_read_tokens: 20, cache_reported_input_tokens: 50})).toMatchObject({hitRate: .4, coverage: .5})
    expect(cacheMetrics({input_tokens: 100, cache_read_tokens: 0, cache_reported_input_tokens: 100}).hitRate).toBe(0)
    expect(cacheMetrics({input_tokens: 100, cache_read_tokens: 0}).hitRate).toBeNull()
  })
  it('preserves explicit contradictions and invalid counts as errors', () => {
    for (const usage of [{input_tokens: 10, cache_read_tokens: 11},
      {input_tokens: 10, cache_read_tokens: 5, cache_reported_input_tokens: 0},
      {input_tokens: 10, cache_read_tokens: 5, cache_reported_input_tokens: 4},
      {input_tokens: 10, cache_reported_input_tokens: 11}, {input_tokens: 10, cache_read_tokens: -1},
      {input_tokens: 10, cache_read_tokens: 1.5}]) {
      expect(cacheMetrics(usage)).toMatchObject({invalid: true, hitRate: null})
    }
  })
})
