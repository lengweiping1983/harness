/** 更新源仅在构建时配置，不在客户端嵌入发布凭据。 */
module.exports = function updateSource(env = process.env, copy = key => key) {
  const repository = env.HARNESS_UPDATE_REPOSITORY
  const address = env.HARNESS_UPDATE_URL
  if (repository && address) throw Error(copy('desktop.release.updateSourceConflict'))
  if (address) {
    let url
    try {url = new URL(address)} catch (cause) {throw Error(copy('desktop.release.updateSourceUrl'), {cause})}
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) throw Error(copy('desktop.release.updateSourceUrl'))
    if (!url.pathname.endsWith('/')) url.pathname += '/'
    return {provider:'generic', url:url.href, channel:'latest'}
  }
  if (!repository || !/^[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+$/.test(repository)) throw Error(copy('desktop.release.updateSourceRequired'))
  const [owner, repo] = repository.split('/')
  return {provider:'github', owner, repo, releaseType:'draft', vPrefixedTagName:true}
}
