import { Children, cloneElement, isValidElement, useId, useMemo, type ReactNode } from 'react'

/** Scope static SVG fragment identities to one mounted icon instance. */
export function IconBody({ children }: { children: ReactNode }) {
  const identity = useId()
  return useMemo(() => {
    function scope(nodes: ReactNode): ReactNode {
      return Children.map(nodes, node => {
        if (!isValidElement<Record<string, unknown>>(node)) return node
        const props: Record<string, unknown> = {}
        for (const [name, value] of Object.entries(node.props)) {
          if (name === 'children') props.children = scope(value as ReactNode)
          else if (typeof value === 'string') {
            if (name === 'id') props[name] = identity + '-' + value
            else props[name] = value.replace(/url\(#([^)]*)\)/g, (_, id: string) => `url(#${identity}-${id})`)
          }
        }
        return cloneElement(node, props)
      })
    }
    return scope(children)
  }, [children, identity])
}
