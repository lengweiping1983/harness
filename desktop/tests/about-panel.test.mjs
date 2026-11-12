import assert from 'node:assert/strict'
import {test} from 'node:test'
import {configureAboutPanel} from '../about-panel.mjs'
import {applicationMenu} from '../application-menu.mjs'

test('native About options use only documented fields and keep platform icon ownership', () => {
  for (const platform of ['darwin', 'win32', 'linux']) {
    const calls = []
    configureAboutPanel({getVersion: () => '1.2.3', setAboutPanelOptions: options => calls.push(options)}, {
      iconPath: '/application/assets/harness.png', platform,
    })
    assert.deepEqual(calls, [{applicationName: 'Harness', applicationVersion: '1.2.3',
      ...(platform === 'darwin' ? {} : {iconPath: '/application/assets/harness.png'})}])
    assert.equal(Object.hasOwn(calls[0], 'applicationIcon'), false)
  }
})

test('About remains a native action in each localized application menu', () => {
  for (const platform of ['darwin', 'win32']) {
    const actions = []
    for (const label of ['About Harness', '关于 Harness', 'About Harness']) {
      const menu = applicationMenu({platform, text: key => key === 'about' ? label : key, command: action => actions.push(action)})
      const item = menu.flatMap(group => group.submenu).find(item => item.label === label)
      assert.equal(item.enabled, true)
      item.click()
    }
    assert.deepEqual(actions, ['about', 'about', 'about'])
  }
})
