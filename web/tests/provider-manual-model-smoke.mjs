import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const provider = {id: 'supplier', type: 'custom', name: 'Manual fixture', connections: [{
  id: 'endpoint', name: 'Manual fixture', protocol: 'openai-completions', base_url: 'https://example.invalid/v1',
  enabled: true, auth: {type: 'api_key', env: {}}, keys: [{id: 'key', enabled: true, mask: '******test'}], models: [],
}]}
const catalog = [{id: 'listed', name: 'Listed model'}]
const entry = `import React from 'react';import {createRoot} from 'react-dom/client';import {SettingsDialog} from '/src/components/overlays/SettingsDialog.tsx';import {useAppStore} from '/src/store/app.ts';import {setLocale,t} from '/src/i18n/index.ts';import '/src/styles.css';await setLocale('en');window.locale=setLocale;window.copy=t;useAppStore.setState({models:[],defaultModel:'',settingsOpen:true,settingsTarget:'models'});createRoot(document.getElementById('root')).render(React.createElement(SettingsDialog));`

await browserScenario(async ({server, browser}) => {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}})
  page.setDefaultTimeout(15000)
  const errors = [], writes = []
  let snapshot = {version: 2, revision: 'initial', providers: [provider]}
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (path === '/api/v1/connection/providers') return route.fulfill({json: {providers: [{id: 'custom', name: 'Custom API', base_url: '', api_key: true, oauth: false, env: [], models: catalog}]}})
    if (path === '/api/v1/codex-gateway') return route.fulfill({json: {accounts: []}})
    if (path === '/api/v1/models') return route.fulfill({json: {models: [], active: '', providers: []}})
    if (path === '/api/v1/ui-settings') return route.fulfill({json: {settings: {default_model: '', model_visibility: {}}}})
    if (path === '/api/v1/connection') {
      if (request.method() === 'GET') return route.fulfill({json: snapshot})
      const body = request.postDataJSON()
      assert.equal(body.revision, snapshot.revision)
      writes.push(body)
      snapshot = {version: 2, revision: 'saved', providers: structuredClone(body.providers)}
      for (const endpoint of snapshot.providers[0].connections) for (const key of endpoint.keys) key.mask = '******test'
      return route.fulfill({json: {routing: snapshot, config: {}}})
    }
    errors.push(`Unexpected request: ${request.method()} ${path}`)
    return route.fulfill({status: 500, json: {error: 'Unexpected fixture request'}})
  })
  const copy = (key, params) => page.evaluate(({key, params}) => window.copy('ui.providers.' + key, params), {key, params})
  const editor = () => page.getByRole('group', {name: /^(Add a model manually|手动添加模型)$/, exact: true})
  const selected = count => page.getByRole('group', {name: new RegExp(`^(${count} models selected|已选择 ${count} 个模型)$`)})
  const input = async key => editor().getByLabel(await copy(key), {exact: true})
  const add = async () => editor().getByRole('button', {name: await copy('addModel'), exact: true})
  await page.goto(server.resolvedUrls.local[0] + '__provider-manual-model')
  await page.getByRole('button', {name: 'Edit', exact: true}).click()
  await editor().waitFor()
  assert.equal(await (await add()).isDisabled(), true)
  await (await input('manualModel')).fill('https://wrong.example.invalid/v1')
  assert.equal(await (await add()).isDisabled(), true)
  await editor().getByRole('alert').waitFor()
  await (await input('manualModel')).fill(' vendor/new ')
  await (await input('modelName')).fill('手动模型 Original')
  for (const invalid of ['0', '-1', '1.5']) {
    await (await input('context_window')).fill(invalid)
    assert.equal(await (await add()).isDisabled(), true)
  }
  await (await input('context_window')).fill('128000')
  await (await input('max_output_tokens')).fill('8192')
  for (const modality of ['image', 'video']) await editor().getByRole('checkbox', {name: await copy(modality), exact: true}).check()
  await (await input('reasoningMode')).selectOption('custom')
  await editor().getByRole('textbox', {name: await copy('reasoningParameter', {level: 'high'}), exact: true}).fill('large')
  for (const level of ['off', 'low', 'medium']) await editor().getByRole('button', {name: await copy('removeReasoning', {level}), exact: true}).click()
  assert.equal(await editor().getByRole('button', {name: await copy('removeReasoning', {level: 'high'}), exact: true}).isDisabled(), true)
  const modelId = await input('manualModel')
  await modelId.dispatchEvent('compositionstart')
  await modelId.press('Enter')
  assert.equal(await selected(0).locator(':scope > div').count(), 0)
  await modelId.dispatchEvent('compositionend')
  await mkdir('../output/model-editor', {recursive: true})
  let matrix = 0
  for (const language of ['en', 'zh-CN', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    await page.setViewportSize({width, height: 1000})
    await page.evaluate(async ({language, theme}) => {await window.locale(language); document.documentElement.className = theme; document.documentElement.dataset.theme = theme}, {language, theme})
    assert.equal(await (await input('manualModel')).inputValue(), ' vendor/new ')
    assert.equal(await (await input('modelName')).inputValue(), '手动模型 Original')
    assert.equal(await (await input('context_window')).inputValue(), '128000')
    assert.equal(await (await input('max_output_tokens')).inputValue(), '8192')
    assert.equal(await (await input('reasoningMode')).inputValue(), 'custom')
    assert.equal(await editor().getByRole('textbox', {name: await copy('reasoningParameter', {level: 'high'}), exact: true}).inputValue(), 'large')
    assert.equal(await (await add()).isDisabled(), false)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false)
    const bounds = await Promise.all([(await input('manualModel')).boundingBox(), (await input('modelName')).boundingBox()])
    assert.ok(bounds.every(Boolean))
    if (width > 1000) assert.ok(Math.abs(bounds[0].y - bounds[1].y) <= 1)
    else assert.ok(bounds[1].y > bounds[0].y + bounds[0].height)
    const region = page.getByRole('region', {name: await copy('models'), exact: true})
    await region.screenshot({path: `../output/model-editor/models-${++matrix}-${language}-${theme}-${width}.png`})
    assert.equal(writes.length, 0)
  }
  await page.evaluate(async () => {await window.locale('en')})
  await page.setViewportSize({width: 1440, height: 1000})
  await (await add()).click()
  assert.equal(await selected(1).locator(':scope > div').count(), 1)
  assert.equal(await (await input('manualModel')).inputValue(), '')
  assert.equal(await (await input('manualModel')).evaluate(element => element === document.activeElement), true)
  assert.equal(await (await input('modelName')).inputValue(), '')
  assert.equal(await (await input('context_window')).inputValue(), '')
  assert.equal(await (await input('reasoningMode')).inputValue(), 'inherit')
  assert.equal(await editor().getByRole('checkbox', {name: 'Image', exact: true}).isChecked(), false)
  assert.equal(await selected(1).locator('details[open]').count(), 0)
  await (await input('manualModel')).fill('vendor/new')
  assert.equal(await (await add()).isDisabled(), true)
  await (await input('manualModel')).press('Enter')
  assert.equal(await selected(1).locator(':scope > div').count(), 1)
  await (await input('manualModel')).fill('vendor/second')
  // 重置草稿会改变布局；双击第二下可能落到新增卡片，焦点返回由上面的单击独立核对。
  await (await add()).dblclick()
  assert.equal(await selected(2).locator(':scope > div').count(), 2)
  await (await input('manualModel')).fill('vendor/third')
  await (await input('manualModel')).press('Enter')
  assert.equal(await selected(3).locator(':scope > div').count(), 3)
  assert.equal(await (await input('manualModel')).evaluate(element => element === document.activeElement), true)
  assert.equal(writes.length, 0, 'Adding models must only update the provider draft')
  await page.getByRole('button', {name: 'Save', exact: true}).click()
  await page.getByRole('button', {name: 'Edit', exact: true}).waitFor()
  assert.equal(writes.length, 1)
  assert.equal(writes[0].providers[0].connections[0].models.length, 3)
  const [first, second, third] = writes[0].providers[0].connections[0].models
  assert.equal(first.model, 'vendor/new')
  assert.equal(first.name, '手动模型 Original')
  assert.equal(first.context_window, 128000)
  assert.equal(first.max_output_tokens, 8192)
  assert.deepEqual(first.input, ['text', 'image', 'video'])
  assert.deepEqual(first.reasoning_efforts, {high: 'large'})
  assert.equal(second.name, 'vendor/second')
  assert.deepEqual(second.input, ['text'])
  assert.equal(second.context_window, undefined)
  assert.equal(second.max_output_tokens, undefined)
  assert.equal(second.reasoning_efforts, undefined)
  assert.notEqual(first.id, second.id)
  assert.equal(third.model, 'vendor/third')
  assert.equal(new Set([first.id, second.id, third.id]).size, 3)
  await page.getByRole('button', {name: 'Edit', exact: true}).click()
  const firstModel = selected(3).locator(':scope > div').first()
  await firstModel.getByText('Model options', {exact: true}).click()
  assert.equal(await firstModel.getByRole('textbox', {name:'Model name',exact:true}).inputValue(), '手动模型 Original')
  assert.equal(await firstModel.getByRole('spinbutton', {name:'Context window',exact:true}).inputValue(), '128000')
  assert.deepEqual(errors, [])
  await page.close()
  console.log('Manual model editor: complete pre-add options, validation, IME, duplicate/double-click admission, draft reset/focus, exact saved parameters/readback and 12 locale/theme/width cases passed')
}, {entry: '/__provider-manual-model-entry.jsx', vite: {plugins: [{name: 'provider-manual-model-fixture',
  resolveId(id) {if (id === '/__provider-manual-model-entry.jsx') return id},
  load(id) {if (id === '/__provider-manual-model-entry.jsx') return entry},
  configureServer(server) {server.middlewares.use('/__provider-manual-model', async (_request, response, next) => {
    try {response.setHeader('Content-Type', 'text/html'); response.end(await server.transformIndexHtml('/__provider-manual-model', '<html><body><div id="root"></div><script type="module" src="/__provider-manual-model-entry.jsx"></script></body></html>'))} catch (error) {next(error)}
  })},
}]}})
