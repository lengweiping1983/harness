import {NativeBrowserError} from './browser-errors.mjs'

export function websiteURL(value) {
  let url
  try {url = new URL(value)} catch (cause) {throw new NativeBrowserError('urlInvalid', {}, {cause})}
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new NativeBrowserError('urlInvalid')
  return url.href
}

export function viewBounds(value, size) {
  if (!value || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(value[key]))) throw new NativeBrowserError('boundsInvalid')
  const x = Math.max(0, Math.min(Math.round(value.x), size[0]))
  const y = Math.max(0, Math.min(Math.round(value.y), size[1]))
  return {x, y, width: Math.max(0, Math.min(Math.round(value.width), size[0] - x)), height: Math.max(0, Math.min(Math.round(value.height), size[1] - y))}
}
