import type {Config} from 'tailwindcss'

/** 为浏览器工程提供 Tailwind 配置的类型入口，供样式断言用例直接复用真实主题。 */
declare const config: Config
export default config
