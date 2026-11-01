import {isRecord} from './guards'
import {responseContracts, responseDefinitions} from './responses.generated'

type Schema = {
  $ref?: string; type?: string | string[]; required?: string[]; properties?: Record<string, Schema>
  items?: Schema; anyOf?: Schema[]; oneOf?: Schema[]; enum?: unknown[]; const?: unknown
  additionalProperties?: boolean | Schema; minimum?: number; maximum?: number
  minLength?: number; maxLength?: number; pattern?: string; minItems?: number; maxItems?: number
}
const definitions = responseDefinitions as Record<string, Schema>
const contracts = (responseContracts as unknown as [string, string, number, Schema][])
  .map(([pattern, method, status, schema]) => ({pattern: new RegExp(pattern), method, status, schema}))
function matches(value: unknown, type: string): boolean {
  switch (type) {
    case 'object': return isRecord(value)
    case 'array': return Array.isArray(value)
    case 'null': return value === null
    case 'number': return typeof value === 'number' && Number.isFinite(value)
    case 'integer': return typeof value === 'number' && Number.isSafeInteger(value)
    default: return typeof value === type
  }
}
function equal(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, index) => equal(item, b[index]))
  if (isRecord(a) && isRecord(b)) return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(key => Object.hasOwn(b, key) && equal(a[key], b[key]))
  return false
}
function invalid(value: unknown, schema: Schema, path = '$'): string | undefined {
  if (schema.$ref) return invalid(value, definitions[schema.$ref], path)
  if (schema.anyOf && !schema.anyOf.some(item => !invalid(value, item, path))) return path
  if (schema.oneOf && schema.oneOf.filter(item => !invalid(value, item, path)).length !== 1) return path
  if (schema.type && !(Array.isArray(schema.type) ? schema.type : [schema.type]).some(type => matches(value, type))) return path
  if (schema.enum && !schema.enum.some(item => equal(value, item))) return path
  if (Object.hasOwn(schema, 'const') && !equal(value, schema.const)) return path
  if (typeof value === 'number' && (schema.minimum !== undefined && value < schema.minimum || schema.maximum !== undefined && value > schema.maximum)) return path
  if (typeof value === 'string' && (schema.minLength !== undefined && Array.from(value).length < schema.minLength || schema.maxLength !== undefined && Array.from(value).length > schema.maxLength || schema.pattern && !new RegExp(schema.pattern).test(value))) return path
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems || schema.maxItems !== undefined && value.length > schema.maxItems) return path
    if (schema.items) for (let index = 0; index < value.length; index++) {
      const error = invalid(value[index], schema.items, `${path}[${index}]`)
      if (error) return error
    }
  }
  if (isRecord(value)) {
    for (const key of schema.required ?? []) if (!Object.hasOwn(value, key)) return `${path}.${key}`
    for (const [key, item] of Object.entries(value)) {
      const child = schema.properties && Object.hasOwn(schema.properties, key) ? schema.properties[key] : undefined
      if (!child && schema.additionalProperties === false) return `${path}.${key}`
      const nested = child ?? (isRecord(schema.additionalProperties) ? schema.additionalProperties : undefined)
      if (nested) {
        const error = invalid(item, nested, `${path}.${key}`)
        if (error) return error
      }
    }
  }
  return undefined
}

/** Validate documented critical responses before a caller can replace its valid state. */
export function responseIssue(path: string, method: string, status: number, payload: unknown): string | undefined {
  const pathname = new URL(path, 'http://harness.local').pathname
  const contract = contracts.find(item => item.method === method.toUpperCase() && item.status === status && item.pattern.test(pathname))
  if (!contract) return undefined
  const error = invalid(payload, contract.schema)
  if (error) return error
  const session = pathname.match(/^\/api\/v1\/sessions\/([^/]+)(?:\/|$)/)?.[1]
  if (session && isRecord(payload)) {
    const identity = payload.session_id ?? (isRecord(payload.permissions) ? payload.permissions.session_id : undefined)
    if (identity !== undefined && identity !== decodeURIComponent(session)) return '$.session_id'
  }
  return undefined
}
