import { describe, expect, it } from 'vitest'
import { persistAgentDraft, readAgentDraft, serializeAgentDraft, type AgentDraft } from '../agentDraft'
import { readTeamMembers, updateTeamMember } from '../teamAuthoring'

const draft: AgentDraft = { version: 5, name: 'writer', displayName: '', profession: '', generatedName: '', description: '写作', prompt: '', model: '', runner: '', personalityPrompt: null, metadataText: '{}', members: '[]', kind: 'agent', scope: 'user', useNow: false, editRevision: null }

describe('Agent skills', () => {
  it('preserves personality text and rejects previous draft schemas without overwriting them', () => {
    const text = 'Use concise explanations.'
    expect(readAgentDraft(serializeAgentDraft({...draft, personalityPrompt: text}))?.personalityPrompt).toBe(text)
    const previous = JSON.stringify({...draft, version: 4})
    let saved = previous
    expect(readAgentDraft(previous)).toBeNull()
    expect(persistAgentDraft({setItem: (_key, value) => { saved = value }, removeItem: () => {}}, 'draft', previous, '')).toBe('invalid')
    expect(saved).toBe(previous)
  })
  it('restores independent and empty selections from unsaved drafts', () => {
    for (const skills of [null, [], ['writer-skill', 'sample:review']]) {
      const storage = new Map<string, string>()
      const selected = serializeAgentDraft({...draft, skills})
      const result = persistAgentDraft({setItem: (key, value) => { storage.set(key, value) }, removeItem: key => { storage.delete(key) }}, 'draft', selected, '')
      expect(result).toBe('saved')
      expect(readAgentDraft(storage.get('draft') ?? null)?.skills).toEqual(skills)
    }
  })
  it('rejects corrupt skill selections and leaves the previous draft recoverable', () => {
    const existing = serializeAgentDraft({...draft, skills: ['writer-skill']})
    let saved = existing
    const invalid = JSON.stringify({...draft, skills: ['writer-skill', 'writer-skill']})
    expect(persistAgentDraft({setItem: (_key, value) => { saved = value }, removeItem: () => {}}, 'draft', invalid, '')).toBe('invalid')
    expect(saved).toBe(existing)
  })
  it('keeps member choices when renaming and promoting another team member', () => {
    const rows = readTeamMembers(JSON.stringify([{name: 'lead', role: 'lead', prompt: '协调', skills: []}, {name: 'writer', role: 'member', agent: 'writer', skills: ['writer-skill']}]))!
    const renamed = updateTeamMember(rows, 1, {name: 'editor', role: 'lead'})
    expect(renamed[0].skills).toEqual([])
    expect(renamed[1].skills).toEqual(['writer-skill'])
    expect(readTeamMembers('[{"name":"lead","role":"lead","skills":"writer-skill"}]')).toBeNull()
  })
})
