import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'

/** 从临时后端的真实目录选择项目，等待正式检出控件就绪，不绕过创建准入。 */
export async function chooseFixtureProject(page) {
  const editor = page.locator('.composer-editor')
  await editor.waitFor()
  const language = await page.locator('html').getAttribute('lang')
  const resources = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url), 'utf8'))
  const copy = key => {assert.equal(typeof resources[key], 'string', key); return resources[key]}
  const response = await page.request.get(new URL('/api/v1/workspaces', page.url()).href)
  assert.ok(response.ok(), 'The isolated fixture project catalog must be readable')
  const {workspaces} = await response.json()
  assert.equal(workspaces.length, 1, 'Conversation fixtures must own exactly one temporary project')
  const workspace = workspaces[0]
  await editor.evaluate(element => element.blur())
  await page.keyboard.press('ControlOrMeta+Shift+u')
  const dock = page.locator('.composer-dock')
  const dialog = page.getByRole('dialog', {name: copy('ui.newConversation.chooseProject'), exact: true})
  const chosen = dock.getByRole('button', {name: workspace.name, exact: true})
  // 新会话会带出项目选择器，弹层要等一帧才挂载完成。立即判定会在遮罩已盖住底座时点击底座按钮，
  // 被遮罩拦截后只能等到超时，看起来像产品缺陷，实际是夹具没等弹层就绪。
  await chosen.or(dialog).first().waitFor({state: 'visible'})
  if (!await chosen.isVisible()) {
    if (!await dialog.isVisible()) {
      await dock.getByRole('button', {name: copy('ui.newConversation.chooseProject'), exact: true}).click()
    }
    await dialog.getByRole('button', {name: workspace.name, exact: true}).click()
    await dialog.getByRole('button', {name: copy('ui.newConversation.useProject'), exact: true}).click()
    await dialog.waitFor({state: 'hidden'})
  }
  await chosen.waitFor()
  await dock.getByText(copy('ui.newConversation.checking'), {exact: true}).waitFor({state: 'hidden'})
  assert.equal(await dock.getByRole('alert').count(), 0, 'The selected fixture directory must pass its checkout check')
  return workspace
}
