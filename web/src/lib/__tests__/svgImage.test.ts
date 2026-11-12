import {expect, it} from 'vitest'
import {parseProgressiveSvg, parseSvgImage} from '../progressiveSvg'

it('preserves numeric SMIL values, transforms, motion paths and synchronization in image-only markup', () => {
  const source = `<svg viewBox="0 0 1600 900"><g id="bow">
    <animateTransform id="draw" attributeName="transform" type="rotate" values="0 400 450;20 400 450;0 400 450" dur="4s" repeatCount="indefinite"/>
    <animate attributeName="opacity" from="0" to="1" values="0;1;0" begin="draw.begin+1s" dur="2s"/>
    <animateMotion path="M0 0 Q600 -200 1200 0" dur="2s"><mpath xlink:href="#trajectory"/></animateMotion>
    <set attributeName="visibility" to="hidden" begin="draw.end"/>
  </g><path id="trajectory" d="M0 0 L100 100"/></svg>`
  const image = parseSvgImage(source)!
  expect(image).not.toHaveProperty('root')
  expect(image.width).toBe(1600)
  expect(image.height).toBe(900)
  for (const value of ['<animateTransform', '<animateMotion', '<set', 'values="0;1;0"', 'from="0"', 'to="1"', 'begin="draw.begin+1s"', 'xlink:href="#trajectory"']) expect(image.markup).toContain(value)
  expect(image.markup).toContain('xmlns="http://www.w3.org/2000/svg"')
  expect(image.markup).toContain('xmlns:xlink="http://www.w3.org/1999/xlink"')
  expect(image.markup).toContain('id="draw"')
  const inline = parseProgressiveSvg(source)!.markup
  for (const tag of ['<animate', '<set', '<mpath']) expect(inline).not.toContain(tag)
})

it('retains CSS keyframes, selectors, CDATA and inline animation styles only for image rendering', () => {
  const source = `<svg viewBox="0 0 100 50"><style><![CDATA[
    @keyframes fly { from { transform: translateX(0); } to { transform: translateX(80px); } }
    .arrow { animation: fly 2s linear infinite; transform-origin: 10px 20px; }
    svg > .arrow { fill: red; }
  ]]></style><rect class="arrow" width="10" height="5" style="animation:fly 2s infinite;transform:rotate(20deg)"/></svg>`
  const image = parseSvgImage(source)!.markup
  expect(image).toContain('@keyframes fly')
  expect(image).toContain('svg &gt; .arrow')
  expect(image).not.toContain('CDATA')
  expect(image).toContain('style="animation:fly 2s infinite;transform:rotate(20deg)"')
  const inline = parseProgressiveSvg(source)!.markup
  expect(inline).not.toContain('<style')
  expect(inline).not.toContain('animation:')
  expect(inline).not.toContain('@keyframes')
})

it('continues stripping scripts, embedded documents, events and dangerous links in image markup', () => {
  const image = parseSvgImage(`<svg viewBox="0 0 100 50" onload="alert(1)"><style>.x{animation:spin 2s infinite}</style>
    <script>alert(2)</script><foreignObject><div>embedded</div></foreignObject>
    <image href="javascript:alert(3)" onerror="alert(4)"/>
    <animate attributeName="href" to="javascript:alert(5)"/>
    <set attributeName="onload" to="alert(6)"/><set attributeName="style" to="background:red"/>
  </svg>`)!.markup
  for (const value of ['<script', 'alert(2)', 'foreignObject', 'embedded', 'onload=', 'onerror=', 'javascript:', '<animate', '<set']) expect(image).not.toContain(value)
  // 动画值只留在图片里，绝不能返回可被 patchSvg 使用的节点树。
  expect(parseSvgImage(image)).not.toHaveProperty('root')
})

it('leaves ordinary static SVG on the incremental path and waits for complete SVG before enabling images', () => {
  expect(parseSvgImage('<svg viewBox="0 0 20 20"><rect width="10"/></svg>')).toBeNull()
  const source = '<svg viewBox="0 0 20 20"><rect width="10"><animate attributeName="x" values="0;10"/>'
  expect(parseSvgImage(source)).toBeNull()
  expect(parseProgressiveSvg(source)!.markup).toContain('<rect width="10">')
})

it('preserves XML entity text and CSS selectors without double escaping, including literal CDATA entities', () => {
  const image = parseSvgImage('<svg viewBox="0 0 20 20"><style>svg &gt; .arrow {fill:red}</style><text>A &amp; B &#x2192; &#20013;</text></svg>')!.markup
  expect(image).toContain('svg &gt; .arrow')
  expect(image).toContain('<text>A &amp; B → 中</text>')
  expect(image).not.toContain('&amp;amp;')
  const cdata = parseSvgImage('<svg><style><![CDATA[.x::after {content:"&amp;"}]]></style></svg>')!.markup
  expect(cdata).toContain('content:"&amp;amp;"')
})
