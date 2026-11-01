/** 用于请求、连接和截图的随机身份；保持无连字符的 UUID 格式。 */
export const newIdentity = () => crypto.randomUUID().replaceAll('-', '')
