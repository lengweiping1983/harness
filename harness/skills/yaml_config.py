"""技能与Agent共用的数据型 YAML 解析，不构造 Python 对象。"""
from harness.i18n import tr

import math
import re

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

from ..core.errors import SkillParseError


class ConfigLoader(yaml.SafeLoader):
    """限制节点与深度，标量使用适合配置的 YAML 1.2 核心语义。"""
    yaml_implicit_resolvers = {}

    def __init__(self, stream):
        super().__init__(stream)
        self.config_depth = self.config_nodes = self.config_aliases = 0

    def compose_node(self, parent, index):
        self.config_depth += 1
        self.config_nodes += 1
        self.config_aliases += int(self.check_event(AliasEvent))
        try:
            if self.config_depth > 48 or self.config_nodes > 20000 or self.config_aliases > 128:
                raise yaml.YAMLError(tr('config.validation.message_1'))
            return super().compose_node(parent, index)
        finally:
            self.config_depth -= 1

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, MappingNode):
            raise yaml.YAMLError(tr('config.validation.message_2'))
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == 'tag:yaml.org,2002:merge':
                continue
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, str):
                raise yaml.YAMLError(tr('config.validation.message_3'))
            if key in seen:
                raise yaml.YAMLError(tr('config.validation.message_4', mapping_key=repr(key[:160])))
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _integer(loader, node):
    """普通前导零按十进制解释，显式进制前缀保持数值语义。"""
    value = loader.construct_scalar(node).replace('_', '')
    sign = -1 if value.startswith('-') else 1
    value = value.lstrip('+-')
    return sign * int(value, 0 if value.startswith(('0x', '0o', '0b')) else 10)


for tag, pattern, initial in [
    ('null', r'^(?:~|null|Null|NULL|)$', ['~', 'n', 'N', '']),
    ('bool', r'^(?:true|True|TRUE|false|False|FALSE)$', list('tTfF')),
    ('int', r'^[-+]?(?:0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|[0-9][0-9_]*)$', list('-+0123456789')),
    ('float', r'^[-+]?(?:[0-9][0-9_]*\.[0-9_]*(?:[eE][-+]?[0-9]+)?|\.[0-9_]+(?:[eE][-+]?[0-9]+)?|[0-9][0-9_]*[eE][-+]?[0-9]+|\.(?:inf|Inf|INF|nan|NaN|NAN))$', list('-+.0123456789')),
    ('merge', r'^(?:<<)$', ['<']),
]:
    ConfigLoader.add_implicit_resolver('tag:yaml.org,2002:' + tag, re.compile(pattern), initial)
ConfigLoader.add_constructor('tag:yaml.org,2002:int', _integer)


def load_mapping(text: str) -> dict:
    """解析单个映射，拒绝循环、重复键及无法写入 JSON 的值。"""
    if len(text.encode('utf-8')) > 256 * 1024:
        raise SkillParseError(tr('config.validation.message_5'))
    try:
        value = yaml.load(text, Loader=ConfigLoader)
        value = {} if value is None else value
        if not isinstance(value, dict):
            raise ValueError(tr('config.validation.message_2'))
        active = set()
        nodes = size = 0
        def validate(item):
            """按展开后的大小计费，阻止短别名产生巨型序列化结果。"""
            nonlocal nodes, size
            nodes += 1
            if nodes > 50000:
                raise ValueError(tr('config.validation.message_6'))
            if isinstance(item, str):
                size += len(item.encode('utf-8'))
                if size > 1024 * 1024:
                    raise ValueError(tr('config.validation.message_7'))
            elif isinstance(item, (dict, list)):
                if id(item) in active:
                    raise ValueError(tr('config.validation.message_8'))
                active.add(id(item))
                for nested in (list(item.keys()) + list(item.values()) if isinstance(item, dict) else item):
                    validate(nested)
                active.remove(id(item))
            elif item is not None and type(item) not in (bool, int, float):
                raise ValueError(tr('config.validation.message_9'))
            elif type(item) is float and not math.isfinite(item):
                raise ValueError(tr('config.validation.message_10'))
        validate(value)
        return value
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        raise SkillParseError(tr('config.validation.message_11', detail=str(exc))) from exc
