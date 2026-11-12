import {loadLocale} from '../src/i18n'

// 双语呈现用例同步切换前显式准备资源；冷加载与竞态由独立用例重置模块验证。
await Promise.all([loadLocale('zh-CN'), loadLocale('en')])
