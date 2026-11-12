import type { PersonalityProfile } from './protocol.generated'

/** 用户人格保存独立副本；比较当前呈现内容，编辑目录后仍须明确应用新版本。 */
export function isSelectedPersonality(profile: PersonalityProfile, selected: PersonalityProfile | null | undefined): boolean {
  return !!selected && profile.id === selected.id && profile.prompt === selected.prompt
    && profile.name === selected.name && profile.description === selected.description
}
