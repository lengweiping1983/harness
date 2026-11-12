import assert from 'node:assert/strict'
import test from 'node:test'
import {readFile} from 'node:fs/promises'
import {mountSecretInput} from '../../extensions/chatgpt-companion/secret-input.mjs'

test('popup pairing token uses an internal icon, preserves its value and translates both states', async () => {
  const handlers = {}, attributes = {}, input = {type: 'password', value: 'Original pairing token'}
  const icons = ['show', 'hide'].map(secretIcon => ({dataset: {secretIcon}, hidden: false}))
  const group = {addEventListener: (type, handler) => {handlers[type] = handler}}
  const button = {dataset: {}, setAttribute: (key, value) => {attributes[key] = value},
    querySelectorAll: () => icons, closest: () => group, addEventListener: group.addEventListener}
  let messages
  const render = mountSecretInput(input, button, key => messages[key].message)
  for (const locale of ['en', 'zh_CN', 'en']) {
    messages = JSON.parse(await readFile(new URL(`../../extensions/chatgpt-companion/_locales/${locale}/messages.json`, import.meta.url)))
    render(false)
    assert.equal(attributes['aria-label'], messages.show_token.message)
    assert.equal(icons[0].hidden, false)
    handlers.click()
    assert.equal(input.type, 'text')
    assert.equal(input.value, 'Original pairing token')
    assert.equal(attributes['aria-label'], messages.hide_token.message)
    assert.equal(attributes['aria-pressed'], 'true')
    assert.equal(icons[1].hidden, false)
    handlers.focusout({currentTarget: {contains: () => true}, relatedTarget: button})
    assert.equal(input.type, 'text')
    handlers.focusout({currentTarget: {contains: () => false}, relatedTarget: null})
    assert.equal(input.type, 'password')
  }
})
