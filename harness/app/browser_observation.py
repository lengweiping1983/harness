"""仅为代理实际观察过的控件提供有界、限于当前页面的引用。

引用不会向 DOM 注入模型提供的属性或脚本。句柄绑定节点标识，
操作前检查也会拒绝被复用或改名的节点。每页仅保留最新观察并释放旧资源。
"""
from __future__ import annotations

import asyncio

from harness.i18n import tr

import contextlib
import uuid
from typing import Any

from ..core.cleanup import join_cleanup, raise_cleanup_errors
from ..core.errors import HarnessError
from .browser_diagnostics import diagnostic_url

# 仅生成简短可读的描述，不实现完整的无障碍名称算法。
# 不读取文本框或密码值；原生下拉选项是有界数据，
# 用于选择和验证选项，名称取自标签、ARIA 或文本。
# 可见性缓存只存在于单次同步求值内；下一次观察或操作前校验重新读取样式。
# 捕获时控件描述与正文扫描共用缓存，避免反复遍历同一条祖先链。
COMPOSED = r"""
  const parent = node => node.assignedSlot || node.parentElement || node.getRootNode()?.host || null;
  const hosts = element => {
    const result = [];
    for (let node = element; node; node = parent(node)) {
      if (node.getRootNode()?.host && !node.parentElement) result.push(node.getRootNode().host);
    }
    return result.reverse();
  };
  const exposed = element => {
    const checked = [];
    let visible = true;
    for (let node = element; node; node = parent(node)) {
      if (visibility.has(node)) { visible = visibility.get(node); break; }
      checked.push(node);
      const style = getComputedStyle(node);
      if (node.matches('[inert],[aria-hidden="true"]') || style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') { visible = false; break; }
    }
    for (const node of checked) visibility.set(node, visible);
    return visible;
  };
  const visibility = new WeakMap();
"""

DESCRIBE = r"""(element, scope = {}) => {
  COMPOSED_FUNCTION
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 240);
  const labelText = node => {
    if (!node || ['INPUT','TEXTAREA','SELECT'].includes(node.tagName)) return '';
    const clone = node.cloneNode(true);
    clone.querySelectorAll('input,textarea,select,button').forEach(control => control.remove());
    return clone.textContent || '';
  };
  const tag = element.tagName.toLowerCase();
  const type = (element.getAttribute('type') || '').toLowerCase();
  const role = element.getAttribute('role') || ({a:'link',button:'button',select:element.multiple?'listbox':'combobox',textarea:'textbox'}[tag]) ||
    (tag === 'input' ? ({checkbox:'checkbox',radio:'radio',submit:'button',button:'button',range:'slider'}[type] || 'textbox') : element.isContentEditable ? 'textbox' : 'control');
  const labelled = clean((element.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => labelText(element.getRootNode().getElementById?.(id))).join(' '));
  const name = labelled || clean(element.getAttribute('aria-label')) ||
    clean(Array.from(element.labels || []).map(labelText).join(' ')) ||
    (['input','textarea','select'].includes(tag) ? '' : clean(element.innerText || element.textContent)) ||
    clean(element.getAttribute('alt')) || clean(element.getAttribute('title')) || clean(element.getAttribute('placeholder'));
  const rect = element.getBoundingClientRect();
  const info = { tag, role, name, type, href: tag === 'a' ? (element.getAttribute('href') || '').slice(0, 500) : '',
    form_action: element.form ? element.form.action : '',
    disabled: Boolean(element.matches(':disabled') || element.getAttribute('aria-disabled') === 'true'),
    connected: element.isConnected && (!scope.snapshot || (() => {const actual = hosts(element), expected = scope.snapshot.items[scope.index]?.hosts || []; return actual.length === expected.length && actual.every((host, i) => host === expected[i]);})()),
    visible: rect.width > 0 && rect.height > 0 && (scope.exposed || exposed)(element),
    checked: ['checkbox','radio'].includes(type) ? Boolean(element.checked) : null };
  const path = hosts(element);
  if (path.length) info.shadow_path = path.map(host => ({tag: host.tagName.toLowerCase(), name: clean(host.getAttribute('aria-label') || host.title || host.id)}));
  if (tag === 'input' && type === 'checkbox') info.indeterminate = Boolean(element.indeterminate);
  if (tag === 'select') {
    info.multiple = Boolean(element.multiple);
    info.options_total = element.options.length;
    info.selected_total = element.selectedOptions.length;
    info.options_offset = scope.offset || 0;
    info.options_query = scope.query || '';
    const query = info.options_query.toLowerCase();
    const options = [];
    let matches = 0;
    for (const option of element.options) {
      if (query && !String(option.label).toLowerCase().includes(query) && !option.value.toLowerCase().includes(query)) continue;
      if (matches >= info.options_offset && options.length < 40) options.push(option);
      matches++;
    }
    info.options_matches = matches;
    info.options_truncated = matches > info.options_offset + options.length;
    info.options = options.map(option => ({
      value: option.value.slice(0, 1000), value_truncated: option.value.length > 1000,
      label: clean(option.label), disabled: Boolean(option.disabled || option.parentElement?.disabled),
      selected: Boolean(option.selected)
    }));
  }
  return info;
}""".replace('COMPOSED_FUNCTION', COMPOSED)

CAPTURE = r"""(settings) => {
  COMPOSED_FUNCTION
  const describe = DESCRIBE_FUNCTION;
  const selector = 'a[href],button,input:not([type="hidden"]),textarea,select,[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="option"],[role="listbox"],[role="textbox"],[role="combobox"],[contenteditable="true"],[tabindex]:not([tabindex="-1"])';
  const items = [], text = [], roots = [{root: document.body || document.documentElement, depth: 0}];
  let truncated = false, scan_truncated = false, scanned = 0, textSize = 0;
  for (let rootIndex = 0; rootIndex < roots.length; rootIndex++) {
    const {root, depth} = roots[rootIndex];
    if (!root) continue;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node = walker.currentNode;
    while (node) {
      if (scanned++ >= settings.scan_limit) { scan_truncated = true; break; }
      if (node.nodeType === Node.ELEMENT_NODE) {
        if (node.shadowRoot && exposed(node)) {
          if (depth < 16) roots.push({root: node.shadowRoot, depth: depth + 1});
          else scan_truncated = true;
        }
        if (node.matches(selector)) {
          if (items.length >= settings.limit) {
            // 已达到引用预算，只判断是否存在额外可见控件；正文扫描继续。
            // 不再计算不会返回的名称、祖先路径和下拉选项。
            const rect = node.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0 && exposed(node)) truncated = true;
          } else {
            const prior = settings.scopes?.items.find(item => item.element === node)?.info;
            const info = describe(node, {offset: prior?.options_offset || 0, query: prior?.options_query || '', exposed});
            if (info.visible) items.push({ element: node, hosts: hosts(node), info });
          }
        }
      } else if (textSize < settings.text_limit && node.textContent.slice(0, settings.text_limit - textSize).trim()) {
        const owner = node.parentElement || root.host;
        // Never read field values/default textarea text, scripts, or hidden text.
        if (owner && !owner.closest('script,style,noscript,template,input,textarea') && exposed(owner)) {
          const range = document.createRange(); range.selectNodeContents(node);
          if (range.getClientRects().length) {
            const value = node.textContent.slice(0, settings.text_limit - textSize).replace(/\s+/g, ' ').trim();
            if (value) { text.push(value); textSize += value.length + 1; }
          }
        }
      }
      node = walker.nextNode();
    }
    if (scanned > settings.scan_limit) break;
  }
  return { items, truncated, scan_truncated, scanned: Math.min(scanned, settings.scan_limit),
    text: text.join(' ').slice(0, settings.text_limit), scroll: {x: window.scrollX, y: window.scrollY} };
}""".replace('DESCRIBE_FUNCTION', DESCRIBE).replace('COMPOSED_FUNCTION', COMPOSED)

RESOLVE = r"""(snapshot, index) => {
  COMPOSED_FUNCTION
  const item = snapshot.items[index];
  if (!item) return null;
  const actual = hosts(item.element), expected = item.hosts || [];
  return actual.length === expected.length && actual.every((host, i) => host === expected[i]) ? item.element : null;
}""".replace('COMPOSED_FUNCTION', COMPOSED)

FRAME_CAPTURE = r"""element => {
  COMPOSED_FUNCTION
  const describe = DESCRIBE_FUNCTION;
  return {element, hosts: hosts(element), info: describe(element), name: element.getAttribute('name') || '',
    title: element.getAttribute('title') || '', src: element.getAttribute('src') || '', srcdoc: element.getAttribute('srcdoc') || ''};
}""".replace('DESCRIBE_FUNCTION', DESCRIBE).replace('COMPOSED_FUNCTION', COMPOSED)

FRAME_VERIFY = r"""snapshot => {
  const describe = DESCRIBE_FUNCTION;
  const resolve = RESOLVE_FUNCTION;
  const element = resolve({items: [snapshot]}, 0);
  if (!element) return false;
  const current = describe(element);
  return current.connected && current.visible && current.name === snapshot.info.name && current.role === snapshot.info.role && JSON.stringify(current.shadow_path) === JSON.stringify(snapshot.info.shadow_path) &&
    (element.getAttribute('name') || '') === snapshot.name && (element.getAttribute('title') || '') === snapshot.title &&
    (element.getAttribute('src') || '') === snapshot.src && (element.getAttribute('srcdoc') || '') === snapshot.srcdoc;
}""".replace('DESCRIBE_FUNCTION', DESCRIBE).replace('RESOLVE_FUNCTION', RESOLVE)


class FrameGuard:
    """绑定的 iframe 容器，导航或重新挂载会使其控件引用失效。"""
    def __init__(self, frame, handle):
        self.frame, self.handle, self.url = frame, handle, frame.url

    async def verify(self):
        if self.frame.is_detached() or self.frame.url != self.url or not await self.handle.evaluate(FRAME_VERIFY):
            raise stale()


SELECTION_MATCHES = """(element, values) => element.selectedOptions.length === values.length &&
  Array.from(element.selectedOptions).every(option => values.includes(option.value))"""


def stale() -> HarnessError:
    return HarnessError(tr('python.browser_observation.675b3ea6b4fd'), code='browser/target-stale')


class BrowserObservation:
    def __init__(self, page_id: str, handle: Any, descriptors: list[dict], truncated: bool):
        self.page_id, self.handle = page_id, handle
        self.generation = uuid.uuid4().hex[:16]
        self.descriptors = descriptors
        self.truncated = truncated
        self.disposed = False
        self._dispose_task: asyncio.Task | None = None
        self.scroll = {'x': 0, 'y': 0}
        self.allowed_indexes = set(range(len(descriptors)))
        self.text = ''
        self.warnings: list[str] = []
        self._entries: dict[int, tuple[Any, int, tuple[FrameGuard, ...]]] = {}
        self._documents: dict[Any, Any] = {}
        self._handles: list[Any] = []

    @classmethod
    async def capture(cls, page, page_id: str, *, scopes=None):
        observation = cls(page_id, None, [], False)
        budget, text_budget = 20000, 30000
        main = getattr(page, 'main_frame', None)
        pending = [(main, (), [])]
        try:
            visited, probed = 0, 0
            while pending and visited < 20:
                frame, guards, path = pending.pop(0)
                visited += 1
                executor = page if frame is main else frame
                handle = None
                try:
                    for guard in guards:
                        await guard.verify()
                    prior = scopes._documents.get(frame) if scopes and not scopes.disposed else None
                    settings = {'limit': max(0, 80 - len(observation.descriptors)), 'scan_limit': budget,
                                'text_limit': text_budget, 'scopes': prior}
                    try:
                        handle = await executor.evaluate_handle(CAPTURE, settings)
                    except Exception:
                        if prior is None:
                            raise
                        # 选项处理器可能触发导航，应读取其新的执行环境，
                        # 不能重放已经完成的输入操作。
                        settings['scopes'] = None
                        handle = await executor.evaluate_handle(CAPTURE, settings)
                    observation._handles.append(handle)
                    view = await handle.evaluate('snapshot => ({descriptors:snapshot.items.map(item => item.info), truncated:snapshot.truncated, scroll:snapshot.scroll, text:snapshot.text, scanned:snapshot.scanned, scan_truncated:snapshot.scan_truncated})')
                    for guard in guards:
                        await guard.verify()
                    observation._documents[frame] = handle
                    if frame is main:
                        observation.handle = handle
                        observation.scroll = view.get('scroll', {'x': 0, 'y': 0})
                    for local, descriptor in enumerate(view['descriptors']):
                        index = len(observation.descriptors)
                        if path:
                            descriptor['frame_path'] = path
                        observation.descriptors.append(descriptor)
                        observation._entries[index] = (handle, local, guards)
                    observation.truncated |= bool(view['truncated'])
                    budget = max(0, budget - view.get('scanned', 0))
                    if view.get('scan_truncated'):
                        observation.warnings.append(tr('python.browser_observation.5d404ffef2f4'))
                    text = view.get('text', '')
                    if text and text_budget:
                        prefix = ('\n[iframe ' + ' / '.join(str(item['index']) + ': ' + (item['title'] or item['name'] or item['url'] or tr('python.browser_observation.97abfb572beb')) for item in path) + ']\n') if path else ''
                        chunk = (prefix + text)[:text_budget]
                        observation.text += chunk
                        text_budget -= len(chunk)
                except Exception:
                    if handle is not None and frame not in observation._documents:
                        with contextlib.suppress(Exception):
                            await handle.dispose()
                        observation._handles.remove(handle)
                    if frame is main:
                        raise
                    observation.warnings.append(tr('python.browser_observation.3e57dcf4287b'))
                    continue
                children = list(getattr(frame, 'child_frames', [])) if frame else []
                if len(path) >= 8 and children:
                    observation.warnings.append(tr('python.browser_observation.77c4703af1b0'))
                    continue
                for ordinal, child in enumerate(children, 1):
                    if probed >= 19:
                        observation.warnings.append(tr('python.browser_observation.a101392c4aca'))
                        break
                    probed += 1
                    container = snapshot = None
                    try:
                        container = await child.frame_element()
                        snapshot = await container.evaluate_handle(FRAME_CAPTURE)
                        observation._handles.append(snapshot)
                        info = await snapshot.evaluate('snapshot => ({visible: snapshot.info.visible, connected: snapshot.info.connected, name: snapshot.name.slice(0,240), title: (snapshot.title || snapshot.info.name).slice(0,240)})')
                        if not info['visible'] or not info['connected']:
                            await snapshot.dispose()
                            observation._handles.remove(snapshot)
                            snapshot = None
                            continue
                        guard = FrameGuard(child, snapshot)
                        pending.append((child, guards + (guard,), path + [{'index': ordinal, 'name': info['name'], 'title': info['title'], 'url': diagnostic_url(child.url)}]))
                    except Exception:
                        if snapshot is not None:
                            with contextlib.suppress(Exception):
                                await snapshot.dispose()
                            observation._handles.remove(snapshot)
                        observation.warnings.append(tr('python.browser_observation.3e57dcf4287b'))
                    finally:
                        if container is not None:
                            with contextlib.suppress(Exception):
                                await container.dispose()
            observation.warnings = list(dict.fromkeys(observation.warnings))
            observation.truncated |= bool(observation.warnings)
            observation.allowed_indexes = set(range(len(observation.descriptors)))
            return observation
        except BaseException:
            await observation.dispose()
            raise

    def _source(self, index):
        return self._entries.get(index, (self.handle, index, ()))

    async def _verify(self, index):
        for guard in self._source(index)[2]:
            await guard.verify()

    def _index(self, ref: str, page_id: str) -> int:
        if self.disposed or page_id != self.page_id or not isinstance(ref, str) or len(ref) > 80:
            raise stale()
        prefix, separator, suffix = ref.partition(':')
        if prefix != self.generation or separator != ':' or not suffix.isascii() or not suffix.isdecimal():
            raise stale()
        index = int(suffix)
        if index not in self.allowed_indexes:
            raise stale()
        return index

    def targets(self) -> list[dict]:
        return [{'ref': f'{self.generation}:{index}', **{k: v for k, v in descriptor.items()
                 if k not in {'connected', 'visible', 'form_action', 'tag'} and (v not in ('', None, False) or k in {'role', 'name', 'multiple', 'options_offset', 'options_query', 'options_total', 'options_matches', 'options_truncated', 'selected_total', 'indeterminate'} or k == 'checked' and v is not None)}}
                for index, descriptor in enumerate(self.descriptors) if index in self.allowed_indexes]

    @staticmethod
    def _fingerprint(descriptor):
        value = {k: descriptor.get(k) for k in ('tag', 'role', 'name', 'type', 'href', 'form_action', 'disabled', 'shadow_path')}
        if descriptor.get('tag') == 'select':
            value.update({k: descriptor.get(k) for k in ('multiple', 'options_total', 'options_truncated', 'options_offset', 'options_query', 'options_matches')})
            value['options'] = [{k: v for k, v in option.items() if k != 'selected'} for option in descriptor.get('options', [])]
        return value

    async def act(self, page_id: str, ref: str, action: str, text: str = '', *, values: list[str] | None = None, checked: bool | None = None) -> None:
        index = self._index(ref, page_id)
        target = None
        dispatched = False
        try:
            await self._verify(index)
            source, local, _ = self._source(index)
            target = await source.evaluate_handle(RESOLVE, local)
            element = target.as_element()
            if element is None:
                raise stale()
            # 试运行等待正常可操作状态，不实际发送点击。
            # 等待结束后再次检查语义，不能只在等待前检查。
            if action == 'click':
                await element.click(trial=True, timeout=1500)
            elif action == 'check':
                if self.descriptors[index].get('tag') != 'input' or self.descriptors[index].get('type') not in {'checkbox', 'radio'}:
                    raise HarnessError(tr('python.browser_observation.0cd510b7b52b'), code='browser/control-type')
                if type(checked) is not bool or self.descriptors[index].get('type') == 'radio' and not checked:
                    raise HarnessError(tr('python.browser_observation.854b3c1bb2d3'), code='browser/input')
                await element.set_checked(checked, trial=True, timeout=1500)
            await self._verify(index)
            observed = self.descriptors[index]
            current = await element.evaluate(DESCRIBE, {'offset': observed.get('options_offset', 0), 'query': observed.get('options_query', ''), 'snapshot': source, 'index': local})
            if not current.get('connected') or not current.get('visible') or current.get('disabled') or self._fingerprint(current) != self._fingerprint(self.descriptors[index]):
                raise stale()
            if action == 'select':
                self._validate_selection(current, values)
                # 有界快照以外的重复值同样会造成歧义。
                counts = await element.evaluate('(element, values) => values.map(value => {let count=0; for (const option of element.options) {if (option.value === value && ++count > 1) break;} return count;})', values)
                if any(count != 1 for count in counts):
                    raise HarnessError(tr('python.browser_observation.98cdaccbe2bb'), code='browser/option-ambiguous')
                if await element.evaluate(SELECTION_MATCHES, values):
                    return  # 设置相同选项时，不得再次触发页面处理器。
            if action == 'check' and current.get('indeterminate') and current.get('checked') == checked:
                raise HarnessError(tr('python.browser_observation.54c4ef66da26'), code='browser/checkbox-mixed')
            dispatched = True
            if action == 'fill':
                await element.fill(text, timeout=1500)
            elif action == 'click':
                await element.click(timeout=1500)
            elif action == 'check':
                await element.set_checked(checked, timeout=1500)
                if await element.is_checked() != checked or await element.evaluate('element => Boolean(element.indeterminate)'):
                    raise HarnessError(tr('python.browser_observation.4751f2ef2c06'), code='browser/state-mismatch')
            elif action == 'select':
                await element.select_option(value=values, timeout=1500)
                if not await element.evaluate(SELECTION_MATCHES, values):
                    raise HarnessError(tr('python.browser_observation.421f9e877533'), code='browser/state-mismatch')
        except HarnessError:
            raise
        except Exception as exc:
            if dispatched:
                raise HarnessError(tr('python.browser_observation.ac5049a24485'), code='browser/action-unconfirmed') from exc
            # 导航或替换可能销毁原 JavaScript 执行环境或节点。
            raise stale() from exc
        finally:
            if target is not None:
                with contextlib.suppress(Exception):
                    await target.dispose()

    @staticmethod
    def _validate_selection(descriptor, values):
        if descriptor.get('tag') != 'select':
            raise HarnessError(tr('python.browser_observation.c638198a599c'), code='browser/control-type')
        if (not isinstance(values, list) or len(values) > 20 or any(not isinstance(value, str) or len(value) > 1000 for value in values) or
                len(values) != len(set(values)) or not descriptor.get('multiple') and len(values) != 1):
            raise HarnessError(tr('python.browser_observation.a4f16e21836d'), code='browser/input')
        for value in values:
            matches = [option for option in descriptor.get('options', []) if option['value'] == value]
            if not matches or any(option.get('disabled') or option.get('value_truncated') for option in matches):
                raise HarnessError(tr('python.browser_observation.a71d092e9d74'), code='browser/option-invalid')

    async def options(self, page_id: str, ref: str, *, offset: int = 0, query: str = ''):
        index = self._index(ref, page_id)
        target = None
        try:
            await self._verify(index)
            source, local, _ = self._source(index)
            target = await source.evaluate_handle(RESOLVE, local)
            element = target.as_element()
            if element is None:
                raise stale()
            current = await element.evaluate(DESCRIBE, {'offset': offset, 'query': query, 'snapshot': source, 'index': local})
            await self._verify(index)
            if current.get('tag') != 'select':
                raise HarnessError(tr('python.browser_observation.55e1e3af8965'), code='browser/control-type')
            previous = self.descriptors[index]
            if (not current.get('connected') or not current.get('visible') or
                    any(current.get(key) != previous.get(key) for key in ('tag', 'role', 'name', 'type', 'href', 'form_action', 'shadow_path'))):
                raise stale()
            # 新的局部观察会使之前所有引用失效；在下次全页观察前，
            # 只有这个确切节点可以被寻址。
            await source.evaluate('(snapshot, value) => {snapshot.items[value.index].info = value.info}', {'index': local, 'info': current})
            if 'frame_path' in previous:
                current['frame_path'] = previous['frame_path']
            self.descriptors[index] = current
            self.allowed_indexes = {index}
            self.generation = uuid.uuid4().hex[:16]
            return self.targets()
        except HarnessError:
            raise
        except Exception as exc:
            raise stale() from exc
        finally:
            if target is not None:
                with contextlib.suppress(Exception):
                    await target.dispose()

    async def dispose(self):
        if self._dispose_task is None or self._dispose_task.done():
            self._dispose_task = asyncio.create_task(self._dispose_owned())
        await join_cleanup(self._dispose_task)

    async def _dispose_owned(self):
        """先撤销引用，逐个释放；失败句柄保留以供显式重试。"""
        self.disposed = True
        handles = self._handles or ([self.handle] if self.handle is not None else [])
        errors, failed = [], []
        seen = set()
        for handle in handles:
            if id(handle) in seen:
                continue
            seen.add(id(handle))
            try:
                await handle.dispose()
            except BaseException as error:
                errors.append(error)
                failed.append(handle)
        self._entries.clear()
        self._documents.clear()
        self._handles = failed
        if not any(handle is self.handle for handle in failed):
            self.handle = None
        raise_cleanup_errors(errors)
