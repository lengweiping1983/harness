import type { TurnActivity } from './turnActivity'

const KEY = 'harness.turn-disclosure.v1'
const MAX_SESSIONS = 32
const MAX_TURNS = 100
interface Choice { turn: number; start: number; end: number; open: boolean }
interface SessionChoices { session: string; choices: Choice[] }
interface Saved { version: 1; sessions: SessionChoices[] }
type StorageAccess = Pick<Storage, 'getItem' | 'setItem'>
let fallback: Saved = { version: 1, sessions: [] }
const sequence = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0

function read(storage?: StorageAccess): Saved {
  let raw: string | null
  try { raw = (storage ?? localStorage).getItem(KEY) } catch { return fallback }
  try {
    if (!raw || raw.length > 1000000) return { version: 1, sessions: [] }
    const parsed = JSON.parse(raw)
    if (parsed?.version !== 1 || !Array.isArray(parsed.sessions)) return { version: 1, sessions: [] }
    const sessions: SessionChoices[] = []
    for (const row of parsed.sessions.slice(-MAX_SESSIONS)) {
      if (!row || typeof row.session !== 'string' || !row.session || row.session.length > 256 || !Array.isArray(row.choices)) continue
      const choices: Choice[] = []
      for (const choice of row.choices.slice(-MAX_TURNS)) {
        if (choice && sequence(choice.turn) && choice.turn > 0 && sequence(choice.start) && sequence(choice.end) && choice.end > choice.start && typeof choice.open === 'boolean') {
          choices.push({ turn: choice.turn, start: choice.start, end: choice.end, open: choice.open })
        }
      }
      sessions.push({ session: row.session, choices })
    }
    return { version: 1, sessions }
  } catch { return { version: 1, sessions: [] } }
}

/** Restore only choices attached to this exact committed successful execution. */
export function readTurnDisclosure(session: string | null, turns: Record<number, TurnActivity>, storage?: StorageAccess): Record<number, boolean> {
  const values: Record<number, boolean> = {}
  if (!session) return values
  const saved = read(storage).sessions.find(row => row.session === session)
  for (const choice of saved?.choices ?? []) {
    const activity = turns[choice.turn]
    if (activity?.reason === 'completed' && activity.startSeq === choice.start && activity.endSeq === choice.end) values[choice.turn] = choice.open
  }
  return values
}

/** Batch explicit choices; no prompts, tool results or active execution state are saved. */
export function rememberTurnDisclosures(session: string | null, turns: Record<number, TurnActivity>, overrides: Record<number, boolean>, storage?: StorageAccess): void {
  if (!session || session.length > 256) return
  const changes: Choice[] = []
  for (const [key, open] of Object.entries(overrides)) {
    const turn = Number(key), activity = turns[turn]
    if (typeof open === 'boolean' && sequence(turn) && turn > 0 && activity?.reason === 'completed' &&
        sequence(activity.startSeq) && sequence(activity.endSeq) && activity.endSeq > activity.startSeq) {
      changes.push({ turn, start: activity.startSeq, end: activity.endSeq, open })
    }
  }
  if (!changes.length) return
  const saved = read(storage)
  const existing = saved.sessions.find(row => row.session === session)?.choices ?? []
  const changed = new Set(changes.map(choice => choice.turn))
  const choices = [...existing.filter(choice => !changed.has(choice.turn)), ...changes].slice(-MAX_TURNS)
  const next: Saved = { version: 1, sessions: [...saved.sessions.filter(row => row.session !== session), { session, choices }].slice(-MAX_SESSIONS) }
  fallback = next
  try { (storage ?? localStorage).setItem(KEY, JSON.stringify(next)) } catch { /* Reading remains usable without browser storage. */ }
}
