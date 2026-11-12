/** 只接受完整场景名；拼写错误和空发现不能伪装成通过。 */
export function selectBrowserScenarios(available, requested) {
  if (!available.length) throw new Error('Browser discovery failed: no scenarios registered')
  const shards = requested.filter(name => name.startsWith('--shard='))
  if (shards.length) {
    if (requested.length !== 1) throw new Error('Browser shard must run the complete discovered suite without other selections')
    const match = /^--shard=([1-9]\d*)\/([1-9]\d*)$/.exec(shards[0])
    if (!match) throw new Error('Invalid browser shard: ' + shards[0])
    const index = Number(match[1]), count = Number(match[2])
    if (!Number.isSafeInteger(index) || !Number.isSafeInteger(count) || index > count || count > available.length) {
      throw new Error('Invalid browser shard: ' + shards[0])
    }
    return [...available].sort().filter((_, position) => position % count === index - 1)
  }
  const unknown = requested.filter(name => !available.includes(name))
  if (unknown.length) throw new Error('Unknown browser scenarios: ' + unknown.join(', '))
  return requested.length ? [...new Set(requested)].sort() : [...available].sort()
}
