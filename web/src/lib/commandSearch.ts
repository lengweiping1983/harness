/** Subsequence matching for command titles and their visible path hints. */
function fuzzy(text: string, query: string): number {
  let position = 0, score = 0, streak = 0
  for (const character of query) {
    const at = text.indexOf(character, position)
    if (at < 0) return 0
    streak = at === position ? streak + 1 : 0
    score += 1 + streak * 2 + (at === 0 ? 3 : 0)
    position = at + character.length
  }
  return score
}
export function searchCommands<T extends { title: string; hint?: string; keywords?: string }>(commands: T[], query: string, limit = 40): T[] {
  const text = query.trim().toLowerCase()
  if (!text) return commands.slice(0, limit)
  return commands.map((command) => ({ command, score: Math.max(fuzzy(command.title.toLowerCase(), text) * 2, fuzzy(command.hint?.toLowerCase() ?? '', text), fuzzy(command.keywords?.toLowerCase() ?? '', text)) }))
    .filter((row) => row.score > 0).sort((a, b) => b.score - a.score).slice(0, limit).map((row) => row.command)
}
