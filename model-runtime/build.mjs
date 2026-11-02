import {fileURLToPath} from 'node:url';
import {deepSeekProviders} from './deepseek.mjs';
import {build} from 'esbuild';
import {getSupportedThinkingLevels} from '@earendil-works/pi-ai/models';
import {mkdir, writeFile, readFile, readdir} from 'node:fs/promises';
import {builtinModels} from '@earendil-works/pi-ai/providers/all';
const destination = new URL('../harness/llm/runtime/', import.meta.url);
const checking = process.argv.includes('--check');
if (!checking) await mkdir(destination, {recursive: true});
async function publish(name, content) {const target = new URL(name, destination); if (checking) {const actual = await readFile(target); if (!Buffer.from(content).equals(actual)) throw Error(`Model runtime is out of sync: ${name}`);} else await writeFile(target, content);}
const result = await build({absWorkingDir: fileURLToPath(new URL('.', import.meta.url)), write: false, metafile: true, entryPoints: [fileURLToPath(new URL('bridge.mjs', import.meta.url))], bundle: true, platform: 'node', format: 'esm', target: 'node22', outfile: fileURLToPath(new URL('bridge.mjs', destination)), banner: {js: "import {createRequire as __harnessRequire} from 'node:module'; const require = __harnessRequire(import.meta.url);"}, minify: false, legalComments: 'eof', plugins: [{name: 'harness-provider-status', setup(builder) {builder.onLoad({filter: /pi-ai[/\\]dist[/\\](?:api[/\\].*|auth[/\\]oauth[/\\]load)\.js$/}, async args => {let contents = await readFile(args.path, 'utf8'); contents = `import {errorIdentity as __harnessErrorIdentity} from ${JSON.stringify(fileURLToPath(new URL('stream-observation.mjs', import.meta.url)))};\n` + contents; contents = contents.replace(/(?:importOAuthModule|importNodeOnlyApi)\("(\.\/[^"]+)\.ts"\)/g, 'import("$1.js")'); contents = contents.replace(/(output\.errorMessage = (?:formatProviderError\(normalizeProviderError\(error\)|formatBedrockError\(error\)|error instanceof Error))/g, "output.harnessError = __harnessErrorIdentity(error); output.harnessStatus = Number(error?.status ?? error?.$metadata?.httpStatusCode ?? 0); $1"); contents = contents.replace('const errorMessage = formatProviderError(normalizeProviderError(error),', 'output.harnessError = __harnessErrorIdentity(error); output.harnessStatus = Number(error?.status ?? error?.$metadata?.httpStatusCode ?? 0); const errorMessage = formatProviderError(normalizeProviderError(error),'); if (args.path.replaceAll('\\', '/').endsWith('/openai-completions.js')) {contents = contents.replace('let delta = \"\";\n                            if (toolCall.function?.arguments)', 'const thoughtSignature = toolCall.extra_content?.google?.thought_signature; if (typeof thoughtSignature === \"string\") block.thoughtSignature = thoughtSignature;\n                            let delta = \"\";\n                            if (toolCall.function?.arguments)'); contents = contents.replace('arguments: JSON.stringify(tc.arguments),\n                        },', 'arguments: JSON.stringify(tc.arguments),\n                        },\n                        ...(tc.thoughtSignature && !parseLegacyEncryptedReasoningDetail(tc.thoughtSignature) ? {extra_content: {google: {thought_signature: tc.thoughtSignature}}} : {}),');} return {contents, loader: 'js'};});}}]});
for (const required of ['api/bedrock-converse-stream.js', ...['anthropic', 'openai-codex', 'openai-chatgpt', 'github-copilot', 'openrouter', 'kimi-coding', 'meta', 'xai', 'radius'].map(name => `auth/oauth/${name}.js`)]) {if (!Object.keys(result.metafile.inputs).some(name => name.endsWith(`/pi-ai/dist/${required}`))) throw Error(`Missing bundled provider module: ${required}`);}
await publish('bridge.mjs', result.outputFiles[0].contents);
const metadata = JSON.parse(await readFile(new URL('provider-metadata.json', import.meta.url), 'utf8'));
const models = builtinModels();
const directory = models.getProviders().map(provider => ({env: [...new Set([...(metadata[provider.id]?.env ?? []), ...(metadata[provider.id]?.baseURLEnv ?? [])])], id: provider.id, name: provider.name, base_url: provider.baseUrl ?? '', dynamic: !!provider.refreshModels, api_key: !!provider.auth.apiKey, oauth: !!provider.auth.oauth, models: provider.getModels().map(model => ({...model, reasoningEfforts: model.reasoning ? getSupportedThinkingLevels(model) : []}))}));
await publish('catalog.json', JSON.stringify([...deepSeekProviders, ...directory]) + '\n');
await publish('package.json', '{"type":"module"}\n');
await publish('LICENSE.pi-ai', await readFile(new URL('licenses/pi-ai.txt', import.meta.url)));

const packageNames = [...new Set(Object.keys(result.metafile.inputs).map(name => /^node_modules\/((?:@[^/]+\/)?[^/]+)\//.exec(name)?.[1]).filter(Boolean))].sort();
const notices = ['@earendil-works/pi-ai — https://github.com/earendil-works/pi\n' + await readFile(new URL('licenses/pi-ai.txt', import.meta.url), 'utf8'), 'DeepSeek Harness source-derived metadata and protocol implementation\n' + await readFile(new URL('licenses/DeepSeek.txt', import.meta.url), 'utf8')];
for (const name of packageNames) {
  const root = new URL(`node_modules/${name}/`, import.meta.url);
  const manifest = JSON.parse(await readFile(new URL('package.json', root), 'utf8'));
  const files = (await readdir(root)).filter(name => /^(license|licence|copying|notice)(?:[.-]|$)/i.test(name)).sort();
  const texts = await Promise.all(files.map(name => readFile(new URL(name, root), 'utf8').catch(() => '')));
  notices.push(`${manifest.name}@${manifest.version}\nLicense: ${manifest.license || 'See source notices'}\n${texts.join('\n')}`);
}
await publish('THIRD-PARTY-NOTICES.txt', notices.join('\n\n========================================\n\n') + '\n');
