import { create } from 'zustand'
import type { WorktreeStatusFilter } from '@/lib/worktreeCatalog'

type Filters = { query: string; status: WorktreeStatusFilter; currentProjectOnly: boolean }
export const useWorktreeCatalogFilters = create<Filters & { update: (values: Partial<Filters>) => void }>((set) => ({
  query: '', status: 'all', currentProjectOnly: false,
  update: values => set(values),
}))
