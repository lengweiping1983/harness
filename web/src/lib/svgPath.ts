/** Keep only received, complete SVG path segments; never guess a split number. */
const ARITY: Record<string, number> = { M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7 }
const NUMBER = /[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?/y
const SPACE = /[\t\n\r ]/

/** `terminated` means the attribute's closing quote has arrived. */
export function pathDataPrefix(data: string, terminated = false): string {
  let offset = 0, end = 0, command = '', first = true
  const whitespace = () => { while (SPACE.test(data[offset] ?? '')) offset += 1 }
  const result = () => data.slice(0, end).trim()
  while (offset < data.length) {
    whitespace()
    if (offset === data.length) break
    const explicit = /[A-Za-z]/.test(data[offset]!)
    if (explicit) {
      command = data[offset++]!
      const upper = command.toUpperCase()
      if (first && upper !== 'M') return result()
      if (upper === 'Z') { end = offset; command = ''; continue }
      if (!ARITY[upper]) return result()
    } else if (!command) return result()

    const upper = command.toUpperCase(), count = ARITY[upper]!
    for (let index = 0; index < count; index += 1) {
      whitespace()
      // Commas may separate parameters/groups, but cannot start a command.
      if (data[offset] === ',') {
        if (explicit && index === 0) return result()
        offset += 1
        whitespace()
      }
      const flag = upper === 'A' && (index === 3 || index === 4)
      if (flag) {
        if (data[offset] !== '0' && data[offset] !== '1') return result()
        offset += 1
      } else {
        NUMBER.lastIndex = offset
        const number = NUMBER.exec(data)
        if (!number || !Number.isFinite(Number(number[0]))) return result()
        offset = NUMBER.lastIndex
        // An exponent/decimal may still be arriving. Without a delimiter or
        // closing quote, `1` must not become a provisional `100` coordinate.
        if ((offset === data.length && !terminated) || /[eE]/.test(data[offset] ?? '')) return result()
        if (upper === 'A' && index < 2 && Number(number[0]) < 0) return result()
      }
    }
    // Reject a malformed suffix before accepting this segment. Signs and a
    // second decimal point can legitimately start a compact next number.
    if (offset < data.length && !/[\t\n\r ,A-Za-z+\-.\d]/.test(data[offset]!)) return result()
    end = offset
    first = false
    if (upper === 'M') command = command === 'M' ? 'L' : 'l'
  }
  return result()
}
