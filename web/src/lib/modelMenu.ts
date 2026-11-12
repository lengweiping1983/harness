import type { ModelInfo } from '@/types'

/**
 * 模型菜单的纯逻辑。
 *
 * 抽出来是为了能在没有 DOM 的情况下验证"分组顺序 / 过滤 / 大小写"这些
 * 最容易出边界错的规则（模型 id 大小写混用很常见）。
 */

/** 按 id 或 label 过滤；空查询原样返回（保持引用稳定，避免无谓重渲染）。 */
export function filterModels(models: ModelInfo[], query: string): ModelInfo[] {
  const q = query.trim().toLowerCase()
  if (!q) return models
  return models.filter((model) =>
    [model.id, model.label, model.provider, model.description]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(q)),
  )
}

export interface ModelGroup {
  provider: string | null
  models: ModelInfo[]
}

/**
 * 按 provider 分组。
 *
 * 组顺序 = 该 provider 首个模型在原列表中的位置。目录里的排序是"用户配置
 * 顺序"（默认模型置顶），按它分组能保证**当前模型永远落在第一组**，
 * 而按字母排会把默认模型埋进中间某个组里。
 */
export function groupModelsByProvider(models: ModelInfo[]): ModelGroup[] {
  const groups: ModelGroup[] = []
  const index = new Map<string | null, ModelGroup>()
  for (const model of models) {
    const provider = model.provider || null
    const existing = index.get(provider)
    if (existing) existing.models.push(model)
    else {
      const group: ModelGroup = { provider, models: [model] }
      index.set(provider, group)
      groups.push(group)
    }
  }
  return groups
}

/** 展示名称不回退到内部路由身份；请求和保存仍使用原 ID。 */
export function modelDisplayName(id: string, models: ModelInfo[], unavailable: string): string {
  const label = models.find(model => model.id === id)?.label
  return label && !label.startsWith('harness/') ? label : id.startsWith('harness/') ? unavailable : label || id
}
