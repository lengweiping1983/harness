"""可移植Agent配置的创作与独占发布。"""
from __future__ import annotations
import dataclasses
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from harness.i18n import tr
from ..core.errors import ValidationError
from .parser import AgentDefinition, is_valid_agent_name, parse_agent_text
from .registry import AgentRegistry


def _invalid(field: str) -> ValidationError:
    return ValidationError(tr("agent.authoring.invalid", field=field))


def _text(data: dict[str, Any], field: str, limit: int, *, required: bool = False) -> str:
    value=data.get(field, "")
    if value is None and not required:value=""
    if not isinstance(value,str) or len(value)>limit or required and not value.strip():
        raise _invalid(field)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(field) from exc
    return value.strip()


def markdown(agent: AgentDefinition) -> str:
    """使用 JSON 形式的 frontmatter，保留换行、引号及反斜线。"""
    header={"name":agent.name,"description":agent.description,"agent_type":agent.agent_type}
    if agent.model:
        header["model"]=agent.model
    if agent.skills is not None:
        header['skills'] = list(agent.skills)
    if agent.metadata:
        header["metadata"]=dict(agent.metadata)
    if agent.members:
        header["members"]=[member.to_dict() for member in agent.members]
    return "---\n"+json.dumps(header,ensure_ascii=False,indent=2,allow_nan=False)+"\n---\n\n"+agent.system_prompt+"\n"


def draft(data: Any, registry: AgentRegistry) -> AgentDefinition:
    """验证结构与成员引用，始终不改变文件和工具权限。"""
    if not isinstance(data,dict):
        raise _invalid("body")
    if set(data)-{"name","description","system_prompt","agent_type","model","members","scope","session_id","metadata","runner","skills"}:
        raise _invalid("fields")
    name=_text(data,"name",64,required=True)
    reserved={"con","prn","aux","nul",*("com"+str(i) for i in range(1,10)),*("lpt"+str(i) for i in range(1,10))}
    if not is_valid_agent_name(name) or name in reserved:
        raise _invalid("name")
    description=_text(data,"description",1000)
    prompt=_text(data,"system_prompt",64000)
    model=_text(data,"model",200) or None
    kind=data.get("agent_type","agent")
    if not isinstance(kind,str) or kind not in {"agent","team"}:
        raise _invalid("agent_type")
    members=data.get("members",[])
    if not isinstance(members,list) or len(members)>8:
        raise _invalid("members")
    for member in members:
        if not isinstance(member,dict) or set(member)-{"name","role","agent","prompt","model","depends_on","runner","execution","display_name","access","skills"}:
            raise _invalid("members")
        for field,limit in [("name",64),("role",16),("agent",64),("prompt",16000),("model",200)]:
            _text(member,field,limit)
    metadata=data.get("metadata", {})
    if not isinstance(metadata,dict):
        raise _invalid("metadata")
    try:
        encoded=json.dumps(metadata,ensure_ascii=False,allow_nan=False)
        if len(encoded)>16000:
            raise _invalid("metadata")
        encoded.encode("utf-8")
    except (ValueError,TypeError,UnicodeEncodeError) as exc:
        raise _invalid("metadata") from exc
    if 'runner' in data:
        runner = data['runner']
        if runner not in (None, '', 'codex', 'claude', 'opencode') or runner and (kind != 'agent' or model):
            raise _invalid('runner')
        metadata = dict(metadata)
        metadata.pop('runner', None)
        if runner:
            metadata['runner'] = runner
    header={"name":name,"description":description,"agent_type":kind,"metadata":metadata}
    if 'skills' in data:
        header['skills'] = data['skills']
    if model:header["model"]=model
    if kind=="team":
        header["members"]=members
    elif members:raise _invalid("members")
    parsed=parse_agent_text("---\n"+json.dumps(header,ensure_ascii=False)+"\n---\n"+prompt)
    if parsed.is_team:
        if len(parsed.members)<2 or sum(member.role=="lead" for member in parsed.members)!=1:
            raise _invalid("members")
        for member in parsed.members:
            if member.agent:
                referenced=registry.get(member.agent)
                if referenced.is_team:
                    raise _invalid("members.agent")
            elif not member.prompt:
                raise _invalid("members.prompt")
        from .execution import resolve_team_execution
        resolve_team_execution(parsed, registry)
    return parsed


def create(root: Path, data: Any, registry: AgentRegistry) -> AgentDefinition:
    """验证后一次发布新目录，冲突时不覆盖已有配置。"""
    agent=draft(data,registry)
    root=Path(root).resolve()
    target=root/agent.name
    if target.exists():
        raise FileExistsError(agent.name)
    root.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix=".harness-agent-",dir=root.parent))
    manifest=temporary/"AGENTS.md"
    try:
        manifest.write_text(markdown(agent),encoding="utf-8")
        if target.exists():
            raise FileExistsError(agent.name)
        try:
            os.rename(temporary,target)
        except OSError as exc:
            if target.exists():raise FileExistsError(agent.name) from exc
            raise
        return dataclasses.replace(agent,path=target/"AGENTS.md")
    finally:
        # 只删除本次创建的草稿文件和空目录，保留其他可能出现的文件。
        if temporary.exists():
            manifest.unlink(missing_ok=True)
            try:temporary.rmdir()
            except OSError:pass
