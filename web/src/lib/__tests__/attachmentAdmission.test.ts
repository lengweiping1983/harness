import {describe, expect, it} from 'vitest'
import {admitFile, looksLikeText} from '../attachments'

const encode = (text: string) => new TextEncoder().encode(text)
describe('bounded UTF-8 attachment probes', () => {
  it.each(['é', '中', '😀'])('admits extensionless %s text across partial character boundaries', character => {
    for (let prefix = 0; prefix < 4; prefix += 1) {
      const bytes = encode('a'.repeat(prefix) + character.repeat(5000))
      expect(looksLikeText(bytes)).toBe(true)
      expect(admitFile('原文', bytes)).toMatchObject({ok:true, mediaType:'text/plain'})
      for (let limit = 61; limit <= 65; limit += 1) {
        expect(admitFile('原文', bytes.subarray(0, limit), bytes.length)).toMatchObject({ok:true, mediaType:'text/plain'})
      }
    }
  })
  it('rejects actual incomplete encodings and invalid bytes while allowing only a truncated probe tail', () => {
    for (const bytes of [new Uint8Array([0xe4, 0xb8]), new Uint8Array([0xf0, 0x9f]), new Uint8Array([0x61, 0xff, 0x62]), new Uint8Array([0x61, 0, 0x62])]) {
      expect(looksLikeText(bytes)).toBe(false)
      expect(admitFile('original', bytes).ok).toBe(false)
    }
    expect(admitFile('original', new Uint8Array([0x61, 0xff]), 100).ok).toBe(false)
    expect(admitFile('original', new Uint8Array([0x61, 0xe4, 0xb8]), 100).ok).toBe(true)
  })
})
