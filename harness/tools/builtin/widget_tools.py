"""对话内可视化小组件工具：show_widget 与 widget_guidelines。

把「画一张图 / 做一个可视化」从生成文件降级为零副作用的对话内渲染：
模型提交 SVG、HTML 或声明式图表 JSON，宿主前端在消息流中原地渲染，用户不离开对话。

关键设计：
* 工具自身不读写工作区、不触网，副作用声明为 ``read``，只读任务中同样可用；
* 给模型看的内容块只放简短确认（标题、类型、大小），完整代码只进
  ``describe`` 的界面展示数据，避免同一载体在模型上下文里重复计费；
* 校验在工具层完成并返回双语错误，让模型拿到可修正的明确原因，
  而不是前端渲染失败后才暴露；
* HTML 片段允许内联脚本（前端在 ``sandbox="allow-scripts"`` 的隔离
  iframe 中执行），SVG 必须无脚本（前端内联渲染，脚本会被剥除）。
"""

from __future__ import annotations

from harness.i18n import tr

import re
import json
import math
import xml.etree.ElementTree as ET
from typing import Any

from ...core.errors import ToolExecutionError
from ...llm.types import TextBlock
from ..registry import ToolDefinition, ToolRunContext

#: 单个小组件代码的最大字符数（防御性上限，正常图表远小于此）。
MAX_WIDGET_CHARS = 200_000
#: 标题最大字符数，超长的标题说明模型把正文误填进了标题。
MAX_TITLE_CHARS = 120
#: 加载提示 1~4 条，与前端轮播一致。
MAX_LOADING_MESSAGES = 4

TABLE_KINDS = ("csv", "tsv")

CHART_KINDS = ("echarts", "vega", "vega-lite", "chartjs", "plotly")

_GUIDE_MODULES = ("diagram", "chart", "interactive", "mockup", "art")

#: 仅用于结构探测的宽松匹配；安全性由前端渲染边界最终保证。
_SVG_OPEN = re.compile(r"<svg[\s>]", re.IGNORECASE)


def detect_widget_kind(code: str) -> str:
    """按首个标签判断载体类型：``<svg`` 开头为 SVG，其余按 HTML 片段处理。"""
    prefix = re.sub(r"^(?:\s|\ufeff|<\?xml[^>]*\?>|<!--[\s\S]*?-->)*", "", code)
    return "svg" if _SVG_OPEN.match(prefix) or re.match(r"<!doctype\s+svg(?:\s|>)", prefix, re.IGNORECASE) else "html"


def validate_chart(code: str, kind: str) -> None:
    """声明式图表只接受有界、字段唯一的内联 JSON，不解释函数或加载外部资源。"""
    def fail(key):
        raise ToolExecutionError(tr(key))
    try:
        units = len(code.encode('utf-16-le')) // 2
    except UnicodeEncodeError as exc:
        raise ToolExecutionError(tr('ui.chart.invalidJson')) from exc
    if units > MAX_WIDGET_CHARS:
        fail('ui.chart.tooLarge')

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                fail('ui.chart.invalidJson')
            value[key] = item
        return value

    def constant(_):
        fail('ui.chart.invalidJson')

    try:
        spec = json.loads(code, object_pairs_hook=unique, parse_constant=constant)
    except (ValueError, RecursionError) as exc:
        raise ToolExecutionError(tr('ui.chart.invalidJson')) from exc
    if not isinstance(spec, dict):
        fail('ui.chart.invalidSpec')

    def visit(value, depth):
        if depth > 48:
            fail('ui.chart.tooDeep')
        if isinstance(value, str):
            try:
                value.encode('utf-8')
            except UnicodeEncodeError as exc:
                raise ToolExecutionError(tr('ui.chart.invalidJson')) from exc
        if isinstance(value, (int, float)):
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                fail('ui.chart.invalidJson')
        if isinstance(value, dict):
            for key, item in value.items():
                visit(key, depth + 1)
                if key in ('__proto__', 'prototype', 'constructor'):
                    fail('ui.chart.unsafeKey')
                if key in ('url', 'href', 'src') and isinstance(item, str):
                    fail('ui.chart.inlineData')
                visit(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
    visit(spec, 0)
    valid = {
        'echarts': isinstance(spec.get('series'), (dict, list)),
        'vega': isinstance(spec.get('marks'), list),
        'vega-lite': any(spec.get(key) for key in ('mark', 'layer', 'hconcat', 'vconcat', 'concat', 'facet', 'repeat')),
        'chartjs': isinstance(spec.get('type'), str) and isinstance(spec.get('data'), dict),
        'plotly': isinstance(spec.get('data'), list),
    }
    if kind == 'plotly' and isinstance(spec.get('data'), list) and any(not isinstance(item, dict) or item.get('type', 'scatter') not in ('scatter', 'bar', 'pie') for item in spec['data']):
        fail('ui.chart.plotlyTypes')
    if not valid[kind]:
        fail('ui.chart.invalidSpec')


def validate_table(code: str, kind: str) -> None:
    """单遍校验 CSV／TSV 引号、记录和大小边界，保留原始分隔文本。"""
    try:
        units = len(code.encode('utf-16-le')) // 2
    except UnicodeEncodeError as exc:
        raise ToolExecutionError(tr('ui.table.invalid')) from exc
    if units > MAX_WIDGET_CHARS:
        raise ToolExecutionError(tr('ui.table.tooLarge'))
    text = code.removeprefix('\ufeff')
    delimiter = ',' if kind == 'csv' else '\t'
    quoted = ended = occupied = False
    fields = cells = records = 0
    width = None

    def field():
        nonlocal fields, cells, occupied, ended
        fields += 1
        cells += 1
        occupied = ended = False
        if fields > 100 or cells > 50000:
            raise ToolExecutionError(tr('ui.table.tooLarge'))

    def record():
        nonlocal fields, records, width
        field()
        if width is not None and fields != width:
            raise ToolExecutionError(tr('ui.table.columns'))
        width = fields
        fields = 0
        records += 1
        if records > 10000:
            raise ToolExecutionError(tr('ui.table.tooLarge'))

    index = 0
    while index < len(text):
        char = text[index]
        if quoted:
            if char == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 1
                else:
                    quoted, ended = False, True
        elif char == delimiter:
            field()
        elif char in ('\r', '\n'):
            record()
            if char == '\r' and index + 1 < len(text) and text[index + 1] == '\n':
                index += 1
        elif char == '"':
            if occupied or ended:
                raise ToolExecutionError(tr('ui.table.invalid'))
            quoted = True
        else:
            if ended:
                raise ToolExecutionError(tr('ui.table.invalid'))
            occupied = True
        index += 1
    if quoted:
        raise ToolExecutionError(tr('ui.table.invalid'))
    if text and (occupied or fields or ended or text[-1] not in ('\r', '\n')):
        record()


def validate_widget(title: Any, code: Any, kind: Any, loading: Any) -> tuple[str, str, str, list[str]]:
    """校验并规范化一次小组件提交；任何不满足都以双语错误明确拒绝。"""
    if not isinstance(title, str) or not title.strip():
        raise ToolExecutionError(tr("python.widget_tools.error.title"))
    clean_title = title.strip()
    if len(clean_title) > MAX_TITLE_CHARS:
        raise ToolExecutionError(tr("python.widget_tools.error.title_long", p0=f"{MAX_TITLE_CHARS}"))
    if not isinstance(code, str) or not code.strip():
        raise ToolExecutionError(tr("python.widget_tools.error.code"))
    clean_code = code.strip()
    if len(code) > MAX_WIDGET_CHARS:
        raise ToolExecutionError(tr("python.widget_tools.error.too_large", p0=f"{MAX_WIDGET_CHARS}"))
    if kind in (None, ""):
        resolved = "auto"
    elif isinstance(kind, str) and kind in ("auto", "svg", "html", *CHART_KINDS, *TABLE_KINDS):
        resolved = kind
    else:
        raise ToolExecutionError(tr("python.widget_tools.error.kind"))
    if resolved in CHART_KINDS:
        validate_chart(code, resolved)
    if resolved in TABLE_KINDS:
        validate_table(code, resolved)
    actual = resolved if resolved in (*CHART_KINDS, *TABLE_KINDS) else detect_widget_kind(clean_code)
    if resolved in ("svg", "html") and resolved != actual:
        raise ToolExecutionError(tr("python.widget_tools.error.kind_mismatch", p0=resolved, p1=actual))

    if actual == "svg":
        if re.search(r"<!DOCTYPE|<!ENTITY", clean_code, re.IGNORECASE):
            raise ToolExecutionError(tr("python.widget_tools.error.svg_invalid"))
        try:
            root = ET.fromstring(clean_code)
        except ET.ParseError as exc:
            raise ToolExecutionError(tr("python.widget_tools.error.svg_invalid")) from exc
        if root.tag.rsplit("}", 1)[-1].lower() != "svg":
            raise ToolExecutionError(tr("python.widget_tools.error.svg_single"))
        if not root.get("viewBox") and not (root.get("width") and root.get("height")):
            raise ToolExecutionError(tr("python.widget_tools.error.svg_viewbox"))
        if any(node.tag.rsplit("}", 1)[-1].lower() == "script" for node in root.iter()):
            raise ToolExecutionError(tr("python.widget_tools.error.svg_script"))

    messages: list[str] = []
    if loading is not None:
        if not isinstance(loading, list) or not 1 <= len(loading) <= MAX_LOADING_MESSAGES \
                or any(not isinstance(item, str) or not item.strip() for item in loading):
            raise ToolExecutionError(tr("python.widget_tools.error.loading_messages", p0=f"{MAX_LOADING_MESSAGES}"))
        messages = [item.strip() for item in loading]
    return clean_title, code if actual in (*TABLE_KINDS, *CHART_KINDS) else clean_code, actual, messages


def build_widget_tools() -> list[ToolDefinition]:
    """构造 ``show_widget`` 与 ``widget_guidelines``。"""

    async def show_widget(args: Any, ctx: ToolRunContext) -> dict[str, Any]:
        del ctx  # 纯展示工具：不读取会话、不触碰工作区。
        if not isinstance(args, dict):
            raise ToolExecutionError(tr("python.widget_tools.error.title"))
        title, code, kind, messages = validate_widget(
            args.get("title"), args.get("widget_code"), args.get("kind"), args.get("loading_messages"))
        return {"title": title, "code": code, "kind": kind, "chars": len(code), "loading_messages": messages}

    def widget_content(args: Any, value: Any) -> list[TextBlock]:
        return [TextBlock(text=tr("python.widget_tools.confirm", p0=value["title"], p1=value["kind"], p2=f"{value['chars']}"))]

    def widget_display(args: Any, value: Any) -> dict[str, Any]:
        return {"widget": {key: value[key] for key in ("title", "code", "kind", "chars", "loading_messages")}}

    async def widget_guidelines(args: Any, ctx: ToolRunContext) -> str:
        del ctx
        raw = args.get("modules") if isinstance(args, dict) else None
        if not isinstance(raw, list) or not raw:
            raise ToolExecutionError(tr("python.widget_tools.error.modules"))
        modules = []
        for item in raw:
            if not isinstance(item, str) or item not in _GUIDE_MODULES:
                raise ToolExecutionError(tr("python.widget_tools.error.module_item", p0=repr(item)))
            if item not in modules:
                modules.append(item)
        parts = [tr("python.widget_tools.guide.base")]
        parts.extend(tr(f"python.widget_tools.guide.{name}") for name in modules)
        return "\n\n".join(parts)

    return [
        ToolDefinition(
            schema={
                "description_key": "python.widget_tools.show.description",
                "parameter_keys": {
                    ("properties", "title", "description"): "python.widget_tools.show.param.title",
                    ("properties", "widget_code", "description"): "python.widget_tools.show.param.code",
                    ("properties", "kind", "description"): "python.widget_tools.show.param.kind",
                    ("properties", "loading_messages", "description"): (
                        "python.widget_tools.show.param.loading", {"p0": f"{MAX_LOADING_MESSAGES}"}),
                },
                "name": "show_widget",
                "description": tr("python.widget_tools.show.description"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": tr("python.widget_tools.show.param.title"),
                        },
                        "widget_code": {
                            "type": "string",
                            "description": tr("python.widget_tools.show.param.code"),
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["auto", "svg", "html", *CHART_KINDS, *TABLE_KINDS],
                            "description": tr("python.widget_tools.show.param.kind"),
                        },
                        "loading_messages": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": MAX_LOADING_MESSAGES,
                            "items": {"type": "string"},
                            "description": tr("python.widget_tools.show.param.loading", p0=f"{MAX_LOADING_MESSAGES}"),
                        },
                    },
                    "required": ["title", "widget_code"],
                    "additionalProperties": False,
                },
            },
            execute=show_widget,
            argument_semantics={'widget_code': 'text', 'title': 'text'},
            to_content=widget_content,
            describe=widget_display,
            # 不读写工作区、不访问外部：声明 read 使其在只读任务中可用。
            effect="read",
            concurrency_safe=True,
        ),
        ToolDefinition(
            schema={
                "description_key": "python.widget_tools.guide.description",
                "parameter_keys": {
                    ("properties", "modules", "description"): "python.widget_tools.guide.param.modules",
                },
                "name": "widget_guidelines",
                "description": tr("python.widget_tools.guide.description"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "modules": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "enum": list(_GUIDE_MODULES)},
                            "description": tr("python.widget_tools.guide.param.modules"),
                        },
                    },
                    "required": ["modules"],
                    "additionalProperties": False,
                },
            },
            execute=widget_guidelines,
            effect="read",
            concurrency_safe=True,
        ),
    ]


__all__ = ["build_widget_tools", "detect_widget_kind", "validate_widget", "MAX_WIDGET_CHARS"]
