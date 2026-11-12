export interface ExternalAgentRunner {
  id: 'opencode' | 'codex' | 'claude'
  title: string
  command: string
  args: string[]
  command_path: string | null
  cli_path: string | null
  available: boolean
  authentication: 'not_checked'
  install: string
  docs_url: string
}

export interface ExternalAgentCatalog {
  runners: ExternalAgentRunner[]
  platform: 'windows' | 'posix'
  delegation_enabled: boolean
}

/** Commands contain fixed runner IDs and no shell-interpreted user input. */
export function externalAgentCommand(runner: ExternalAgentRunner['id']): string {
  return 'harness --external-agent ' + runner
}

export function externalRunnerReadiness(catalog: ExternalAgentCatalog | null, runner: string): 'unknown' | 'disabled' | 'missing' | 'detected' {
  if (!catalog) return 'unknown'
  if (!catalog.delegation_enabled) return 'disabled'
  const entry = catalog.runners.find(item => item.id === runner)
  if (!entry) return 'unknown'
  return entry.available ? 'detected' : 'missing'
}
