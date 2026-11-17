/** 回归按用户入口展开所需能力；默认页面的收敛单独由简洁界面场景验证。 */
export async function openAcademySection(panel, section) {
  const details = panel.locator('details[data-academy-section="' + section + '"]').first()
  if (!await details.count()) return
  // 嵌套能力先以真实点击展开祖先 disclosure（如管理），再展开目标 section。
  await details.evaluate(node => {
    const closed = []
    let parent = node.parentElement
    while (parent) {if (parent.tagName === 'DETAILS' && !parent.open) closed.push(parent); parent = parent.parentElement}
    closed.reverse().forEach(el => el.querySelector(':scope > summary')?.click())
  })
  if (await details.getAttribute('open') === null) await details.locator(':scope > summary').click()
}
