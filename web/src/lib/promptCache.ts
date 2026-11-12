import type { TokenUsage } from '@/types'

export function cacheMetrics(usage: Pick<TokenUsage, 'input_tokens' | 'output_tokens' | 'cache_read_tokens' | 'cache_reported_input_tokens'> | null | undefined) {
  const count = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0
  const input = count(usage?.input_tokens)
  const output = count(usage?.output_tokens)
  const read = count(usage?.cache_read_tokens)
  const reported = count(usage?.cache_reported_input_tokens ?? (read > 0 ? usage?.input_tokens : 0))
  const invalid = read > reported || reported > input || [usage?.input_tokens, usage?.output_tokens,
    usage?.cache_read_tokens, usage?.cache_reported_input_tokens].some(value => value !== undefined &&
      (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0))
  return { input, output, read, total: input + output, reported,
    invalid,
    hitRate: !invalid && reported > 0 ? read / reported : null,
    coverage: !invalid && input > 0 ? reported / input : null }
}
