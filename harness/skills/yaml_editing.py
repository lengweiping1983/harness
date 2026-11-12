"""定点修改界面标量，保留策略、依赖和未修改配置的原文。"""
from harness.i18n import tr

import json

import yaml
from yaml.nodes import MappingNode, ScalarNode

from .yaml_config import ConfigLoader, load_mapping


def update_interface(text: str, generated: dict, explicit: set[str]) -> str:
    """只更新显式字段并补齐缺失默认值；别名指向外部定义时明确拒绝。"""
    data = load_mapping(text)
    current = data.get('interface', {})
    if not isinstance(current, dict):
        raise ValueError(tr('config.validation.message_12'))
    updates = {key: value for key, value in generated.items() if key in explicit or key not in current}
    if not updates:
        return text
    root = yaml.compose(text, Loader=ConfigLoader)
    newline = '\r\n' if '\r\n' in text else '\n'
    rendered = lambda value: json.dumps(value, ensure_ascii=False)
    if root is None:
        return 'interface:' + newline + ''.join('  ' + key + ': ' + rendered(value) + newline for key, value in updates.items())
    if not isinstance(root, MappingNode):
        raise ValueError(tr('config.validation.message_13'))
    entry = next(((key, value) for key, value in root.value if key.value == 'interface'), None)
    changes = []
    if entry is None:
        if root.flow_style:
            prefix = ', ' if root.value else ''
            changes.append((root.end_mark.index - 1, root.end_mark.index - 1, prefix + 'interface: ' + rendered({**current, **updates})))
        else:
            position = root.end_mark.index
            indent = root.value[0][0].start_mark.column if root.value else 0
            prefix = '' if position == 0 or text[position - 1] in '\r\n' else newline
            value = prefix + ' ' * indent + 'interface:' + newline
            value += ''.join(' ' * (indent + 2) + key + ': ' + rendered(item) + newline for key, item in {**current, **updates}.items())
            changes.append((position, position, value))
    else:
        key, mapping = entry
        if not isinstance(mapping, MappingNode) or mapping.start_mark.index < key.end_mark.index:
            raise ValueError(tr('config.validation.message_14'))
        fields = {key.value: (key, value) for key, value in mapping.value}
        missing = {}
        for name, value in updates.items():
            if name not in fields:
                missing[name] = value
                continue
            field, scalar = fields[name]
            if not isinstance(scalar, ScalarNode) or scalar.start_mark.index < field.end_mark.index:
                raise ValueError(tr('config.validation.message_15'))
            start, end = scalar.start_mark.index, scalar.end_mark.index
            suffix = newline if text[start:end].endswith('\n') else ''
            changes.append((start, end, rendered(value) + suffix))
        if missing:
            position = mapping.end_mark.index
            if mapping.flow_style:
                position -= 1
                addition = (', ' if mapping.value else '') + ', '.join(key + ': ' + rendered(value) for key, value in missing.items())
            else:
                indent = mapping.value[0][0].start_mark.column
                addition = ('' if position == 0 or text[position - 1] in '\r\n' else newline)
                addition += ''.join(' ' * indent + key + ': ' + rendered(value) + newline for key, value in missing.items())
            changes.append((position, position, addition))
    for start, end, replacement in sorted(changes, reverse=True):
        text = text[:start] + replacement + text[end:]
    checked = load_mapping(text)
    if checked.get('interface') != {**current, **updates} or {key: value for key, value in checked.items() if key != 'interface'} != {key: value for key, value in data.items() if key != 'interface'}:
        raise ValueError(tr('config.validation.message_16'))
    return text
