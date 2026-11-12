/** Product copy is read once for this dedicated Node process, outside the script VM. */
import {readFileSync} from 'node:fs';

const language = process.env.HARNESS_WORKFLOW_LANGUAGE;
if (!['en', 'zh-CN'].includes(language)) throw new Error('workflow/language-required');
const messages = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(readFileSync(new URL(`../../i18n/locales/${language}.json`, import.meta.url))));

export function tr(key, values = {}) {
  if (!Object.hasOwn(messages, key)) throw new Error('workflow/resource-missing');
  return messages[key].replace(/\{([^{}]+)\}/g, (_, name) => {
    if (!Object.hasOwn(values, name)) throw new Error('workflow/resource-argument-missing');
    return String(values[name]);
  });
}
