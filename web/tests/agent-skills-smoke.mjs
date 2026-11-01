import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import {createInterface} from 'node:readline'
import {fileURLToPath} from 'node:url'
import {mkdir, readFile} from 'node:fs/promises'
import {launchBrowser} from './browser-scenario.mjs'
import {fixturePython} from './python-fixture.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
const backend = spawn(fixturePython, ['-m', 'tests.agent_skills_fixture'], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']})
const lines = createInterface({input: backend.stdout})
let browser, timer, activePage, diagnostics = ''
backend.stderr.on('data', value => {diagnostics += value})
try {
  const ready = await new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Error('Agent skills fixture startup timed out: ' + diagnostics)), 20000)
    backend.once('error', reject)
    backend.once('exit', code => reject(Error('Agent skills fixture exited: ' + code)))
    lines.on('line', line => {try {const value = JSON.parse(line); if (value.port) resolve(value)} catch { /* 忽略启动诊断。 */ }})
  })
  clearTimeout(timer)
  browser = await launchBrowser()
  const base = 'http://127.0.0.1:' + ready.port
  const initialSkills = (await (await fetch(base + '/api/v1/plugins/skills')).json()).skills
  for (const skill of initialSkills) if (skill.name !== 'default-skill') {
    const response = await fetch(base + '/api/v1/plugins/skills/' + encodeURIComponent(skill.name) + '/enabled', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: false})})
    assert.ok(response.ok)
  }
  for (const language of ['zh-CN', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
    const page = await browser.newPage({viewport: {width, height: 900}})
    activePage = page
    const translations = JSON.parse(await readFile(new URL('../src/i18n/locales/' + language + '.json', import.meta.url)))
    const t = key => translations[key]
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    assert.ok((await page.request.patch(base + '/api/v1/ui-settings', {data: {language}})).ok())
    await page.addInitScript(theme => localStorage.setItem('harness.ui-theme', theme), theme)
    const suffix = [language.toLowerCase(), theme, width].join('-')
    const name = 'writer-' + suffix, teamName = 'team-' + suffix
    assert.ok((await page.request.post(base + '/api/v1/plugins/skills/writer-skill/enabled', {data: {enabled: false}})).ok())
    for (const payload of [
      {name, description: name, system_prompt: '编写内容'},
      {name: teamName, description: teamName, agent_type: 'team', skills: ['default-skill'], members: [{name: 'lead', role: 'lead', prompt: '协调'}, {name: 'worker', prompt: '执行'}]},
    ]) assert.ok((await page.request.post(base + '/api/v1/plugins/agents/create', {data: {...payload, scope: 'user'}})).ok())
    await page.goto(base)
    await page.getByRole('textbox', {name: t('ui.components.chat.Composer.a410452649b3'), exact: true}).waitFor()
    if (width < 600) await page.getByRole('button', {name: t('ui.sidebar.expand'), exact: true}).click()
    await page.getByRole('button', {name: t('ui.components.sidebar.ProjectSidebar.df3d58c7d84b'), exact: false}).click()
    const settings = page.getByRole('dialog', {name: t('ui.components.overlays.SettingsDialog.df3d58c7d84b'), exact: true})
    const agentsGroup = settings.getByRole('button', {name: t('ui.settings.groups.agents'), exact: true})
    if (await agentsGroup.getAttribute('aria-expanded') === 'false') await agentsGroup.click()
    await settings.getByRole('button', {name: t('ui.agents.directory'), exact: true}).click()
    async function edit(identifier, kind = 'agent') {
      const card = settings.locator('article').filter({hasText: identifier}).first()
      const actions = card.locator('summary').filter({hasText: t('ui.personality.moreActions')})
      if (await actions.locator('..').getAttribute('open') === null) await actions.click()
      await card.getByRole('button', {name: t('ui.agent.author.edit'), exact: true}).click()
      const dialog = page.getByRole('dialog', {name: t('ui.agent.author.' + (kind === 'team' ? 'editTeamTitle' : 'editAgentTitle')), exact: true})
      await dialog.waitFor()
      await page.getByRole('button', {name: t('ui.components.overlays.CommandPalette.58ce29e73319').replace('{p0}', t('ui.agent.author.editLoaded')), exact: true}).click()
      const notifications = page.getByRole('button', {name: new RegExp('^' + t('ui.components.overlays.CommandPalette.58ce29e73319').replace('{p0}', ''))})
      while (await notifications.count()) await notifications.first().click()
      return dialog
    }
    const picker = dialog => dialog.locator('summary').filter({hasText: t('ui.agent.skills.title')}).last().locator('..')
    await settings.getByRole('button', {name: t('ui.agent.author.newAgent'), exact: true}).click()
    const fresh = page.getByRole('dialog', {name: t('ui.agent.author.newAgent'), exact: true})
    await picker(fresh).locator('summary').click()
    await picker(fresh).getByRole('switch', {name: 'default-skill', exact: true}).waitFor()
    assert.equal(await picker(fresh).getByRole('switch', {name: 'default-skill', exact: true}).isChecked(), false)
    assert.equal(await picker(fresh).getByRole('switch', {name: t('ui.agent.skills.inherit'), exact: true}).isChecked(), false)
    const freshPicker = picker(fresh)
    const inherit = freshPicker.getByRole('switch', {name: t('ui.agent.skills.inherit'), exact: true})
    const defaultSkill = freshPicker.getByRole('switch', {name: 'default-skill', exact: true})
    const writerSkill = freshPicker.getByRole('switch', {name: 'writer-skill', exact: true})
    await inherit.check()
    assert.ok(await defaultSkill.isChecked())
    await inherit.uncheck()
    assert.equal(await defaultSkill.isChecked(), false)
    assert.equal(await writerSkill.isChecked(), false)
    await writerSkill.check()
    await inherit.check()
    assert.ok(await defaultSkill.isChecked())
    assert.equal(await writerSkill.isChecked(), false)
    await inherit.uncheck()
    assert.equal(await defaultSkill.isChecked(), false)
    assert.ok(await writerSkill.isChecked())
    await freshPicker.locator('summary').click()
    await freshPicker.locator('summary').click()
    await defaultSkill.waitFor()
    await inherit.check()
    await inherit.uncheck()
    assert.equal(await defaultSkill.isChecked(), false)
    assert.ok(await writerSkill.isChecked())
    await fresh.getByRole('textbox', {name: t('ui.agent.author.friendlyName'), exact: true}).fill('Optional description')
    assert.ok(await fresh.getByRole('button', {name: t('ui.agent.author.createOnly'), exact: true}).isEnabled())
    await fresh.locator('summary').filter({hasText: t('ui.agent.author.advanced')}).click()
    const metadata = fresh.getByRole('textbox', {name: t('ui.agent.author.metadata'), exact: true})
    assert.equal(JSON.parse(await metadata.inputValue()).display_name, 'Optional description')
    await metadata.fill('{')
    assert.ok(await fresh.getByRole('button', {name: t('ui.agent.author.createOnly'), exact: true}).isDisabled())
    await metadata.fill(JSON.stringify({display_name: 'JSON name', custom: true, personality_prompt: '自定义人格正文'}))
    assert.equal(await fresh.getByRole('textbox', {name: t('ui.agent.author.friendlyName'), exact: true}).inputValue(), 'JSON name')
    assert.equal(JSON.parse(await metadata.inputValue()).custom, true)
    await fresh.locator('summary').filter({hasText: t('ui.agent.author.personalityOptional')}).click()
    const personality = fresh.getByRole('textbox', {name: t('ui.personality.content'), exact: true})
    assert.equal(await personality.inputValue(), '自定义人格正文')
    await personality.fill('编辑后的人格正文')
    await page.waitForFunction(({label, content}) => {
      const element = [...document.querySelectorAll('textarea')].find(row => row.getAttribute('aria-label') === label)
      try { return element && JSON.parse(element.value).personality_prompt === content } catch { return false }
    }, {label: t('ui.agent.author.metadata'), content: '编辑后的人格正文'})
    assert.deepEqual(JSON.parse(await metadata.inputValue()), {display_name: 'JSON name', custom: true, personality_prompt: '编辑后的人格正文'})
    await fresh.press('Escape')
    let dialog = await edit(name)
    let panel = picker(dialog)
    // 读取错误保留编辑器；继承开关仍可取消，重试使用同一目录。
    let fail = true
    await page.route('**/api/v1/plugins/skills?view=picker*', route => fail ? route.fulfill({status: 503, json: {error: {code: 'fixture/unavailable', message: 'fixture skill list unavailable'}}}) : route.continue())
    await panel.locator('summary').click()
    await panel.getByRole('alert').waitFor()
    const failedInherit = panel.getByRole('switch', {name: t('ui.agent.skills.inherit'), exact: true})
    assert.ok(await failedInherit.isEnabled())
    await failedInherit.uncheck()
    await failedInherit.check()
    fail = false
    await panel.getByRole('button', {name: t('ui.agent.skills.retry'), exact: true}).click()
    await panel.getByRole('switch', {name: 'default-skill', exact: true}).waitFor()
    assert.equal(await panel.getByRole('switch', {name: 'writer-skill', exact: true}).isChecked(), false)
    const search = panel.getByRole('searchbox', {name: t('ui.agent.skills.search'), exact: true})
    await search.fill('writer')
    await panel.getByRole('button', {name: t('ui.agent.skills.selectVisible'), exact: true}).click()
    assert.ok(await panel.getByRole('switch', {name: 'writer-skill', exact: true}).isChecked())
    await search.fill('')
    await panel.getByRole('switch', {name: 'default-skill', exact: true}).uncheck()
    await search.fill('no matching skill')
    await panel.getByText(t('ui.agent.skills.noMatches'), {exact: true}).waitFor()
    await search.fill('writer')
    await panel.getByRole('switch', {name: 'writer-skill', exact: true}).focus()
    assert.ok(await panel.getByRole('switch', {name: 'writer-skill', exact: true}).isChecked())
    await page.keyboard.press('Space')
    assert.equal(await panel.getByRole('switch', {name: 'writer-skill', exact: true}).isChecked(), false)
    await page.keyboard.press('Space')
    assert.ok(await panel.getByRole('switch', {name: 'writer-skill', exact: true}).isChecked())
    if (process.env.HARNESS_TEST_SCREENSHOTS) {
      await mkdir(process.env.HARNESS_TEST_SCREENSHOTS, {recursive: true})
      await page.screenshot({path: process.env.HARNESS_TEST_SCREENSHOTS + '/agent-skills-' + suffix + '.png'})
    }
    await dialog.getByRole('button', {name: t('ui.agent.author.cancel'), exact: true}).click()
    dialog = await edit(name)
    panel = picker(dialog)
    await panel.locator('summary').click()
    await panel.getByRole('switch', {name: 'writer-skill', exact: true}).waitFor()
    assert.ok(await panel.getByRole('switch', {name: 'writer-skill', exact: true}).isChecked())
    // 空目录不丢失已选技能；恢复目录后继续保存，不需要重新选择。
    await panel.locator('summary').click()
    await page.route('**/api/v1/plugins/skills?view=picker*', route => route.fulfill({json: {skills: [], enabled: true}}))
    await panel.locator('summary').click()
    await panel.getByText(t('ui.agent.skills.empty'), {exact: true}).waitFor()
    assert.ok(await panel.getByRole('switch', {name: 'writer-skill', exact: true}).isChecked())
    await panel.locator('summary').click()
    await page.unroute('**/api/v1/plugins/skills?view=picker*')
    await panel.locator('summary').click()
    await panel.getByRole('switch', {name: 'writer-skill', exact: true}).waitFor()
    await dialog.getByRole('button', {name: t('ui.agent.author.editSave'), exact: true}).click()
    await dialog.waitFor({state: 'hidden'})
    const saved = await (await page.request.get(base + '/api/v1/plugins/agents/' + name + '/edit')).json()
    assert.deepEqual(saved.agent.skills, ['writer-skill'])
    const catalog = await (await page.request.get(base + '/api/v1/plugins/skills')).json()
    assert.equal(catalog.skills.find(skill => skill.name === 'writer-skill').enabled, false)
    // 显式清空保留为空数组；重新打开不会恢复全局默认。
    dialog = await edit(name); panel = picker(dialog)
    await panel.locator('summary').click()
    await panel.getByRole('button', {name: t('ui.agent.skills.clear'), exact: true}).click()
    await panel.getByText(t('ui.agent.skills.noneHint'), {exact: true}).waitFor()
    await dialog.getByRole('button', {name: t('ui.agent.author.editSave'), exact: true}).click()
    await dialog.waitFor({state: 'hidden'})
    assert.deepEqual((await (await page.request.get(base + '/api/v1/plugins/agents/' + name + '/edit')).json()).agent.skills, [])
    // 保存的是继承状态，默认技能变化后重开读取最新选择。
    dialog = await edit(name); panel = picker(dialog)
    await panel.locator('summary').click()
    await panel.getByRole('switch', {name: 'default-skill', exact: true}).waitFor()
    await panel.getByRole('switch', {name: t('ui.agent.skills.inherit'), exact: true}).check()
    await dialog.getByRole('button', {name: t('ui.agent.author.editSave'), exact: true}).click()
    await dialog.waitFor({state: 'hidden'})
    assert.equal((await (await page.request.get(base + '/api/v1/plugins/agents/' + name + '/edit')).json()).agent.skills, null)
    assert.ok((await page.request.post(base + '/api/v1/plugins/skills/writer-skill/enabled', {data: {enabled: true}})).ok())
    dialog = await edit(name); panel = picker(dialog)
    await panel.locator('summary').click()
    await panel.getByRole('switch', {name: 'writer-skill', exact: true}).waitFor()
    assert.ok(await panel.getByRole('switch', {name: t('ui.agent.skills.inherit'), exact: true}).isChecked())
    assert.ok(await panel.getByRole('switch', {name: 'writer-skill', exact: true}).isChecked())
    await dialog.getByRole('button', {name: t('ui.agent.author.cancel'), exact: true}).click()
    assert.ok((await page.request.post(base + '/api/v1/plugins/skills/writer-skill/enabled', {data: {enabled: false}})).ok())
    await settings.getByRole('tab', {name: t('ui.agent.author.teamsLabel'), exact: true}).click()
    dialog = await edit(teamName, 'team')
    assert.equal(await dialog.locator('summary').filter({hasText: t('ui.agent.skills.title')}).count(), 0)
    await dialog.getByRole('button', {name: t('ui.agent.author.editSave'), exact: true}).click()
    await dialog.waitFor({state: 'hidden'})
    const team = (await (await page.request.get(base + '/api/v1/plugins/agents/' + teamName + '/edit')).json()).agent
    assert.deepEqual(team.skills, ['default-skill'])
    assert.equal(team.members.find(member => member.name === 'worker').skills, undefined)
    await settings.getByRole('tab', {name: t('ui.agent.author.agentsLabel'), exact: true}).click()
    dialog = await edit(name)
    const execution = dialog.locator('summary').filter({hasText: t('ui.agent.author.executionSettings')})
    if (await execution.locator('..').getAttribute('open') === null) await execution.click()
    const mode = dialog.getByRole('combobox', {name: t('ui.agent.author.executionMode'), exact: true})
    await mode.selectOption('codex')
    await dialog.getByText(t('ui.agent.skills.externalHint'), {exact: true}).waitFor()
    assert.equal(await dialog.getByRole('searchbox', {name: t('ui.agent.skills.search'), exact: true}).count(), 0)
    assert.equal(await dialog.locator('summary').filter({hasText: t('ui.agent.skills.title')}).count(), 0)
    await mode.selectOption('')
    assert.equal(await picker(dialog).count(), 1)
    await mode.selectOption('codex')
    await dialog.getByRole('button', {name: t('ui.agent.author.editSave'), exact: true}).click()
    await dialog.waitFor({state: 'hidden'})
    const external = (await (await page.request.get(base + '/api/v1/plugins/agents/' + name + '/edit')).json()).agent
    assert.equal(external.runner, 'codex')
    assert.equal(external.skills, null)
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    assert.deepEqual(errors, [])
    await page.close()
    console.log('Agent skills: ' + suffix + ' passed')
  }
} catch (error) {
  if (activePage && !activePage.isClosed()) console.error((await activePage.locator('body').innerText()).slice(-7000))
  throw error
} finally {
  clearTimeout(timer)
  try {await browser?.close()} finally {
    lines.close()
    if (backend.exitCode === null && backend.signalCode === null) {
      const exited = once(backend, 'exit')
      backend.kill('SIGTERM')
      const deadline = setTimeout(() => backend.kill('SIGKILL'), 20000)
      await exited.finally(() => clearTimeout(deadline))
    }
  }
}
