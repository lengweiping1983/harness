/** 回归按用户入口展开所需能力；默认页面的收敛单独由简洁界面场景验证。 */
export async function openAcademySection(panel, section) {
  const details = panel.locator('details[data-academy-section="' + section + '"]').first()
  if (!await details.count()) return
  if (await details.getAttribute('open') === null) await details.locator(':scope > summary').click()
}
