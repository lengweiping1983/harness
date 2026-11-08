"""唯一 Superagent 配置；启动前完成结构验证，不读取凭据或旧账本。"""
from __future__ import annotations

from pathlib import Path
import re

from .superagent_common import _error, _json, _text, _names
from .superagent_authorization import validate_policy
from .superagent_executor import WorkerProfile
from .superagent_http import api_root
from .superagent_source_registry import LocalSourceKind, GitHubSourceKind, FeishuSourceKind


def read_config(path):
    """仅接收当前格式，拒绝旧配置和未知字段，不执行隐式转换。"""
    path = Path(path).expanduser().resolve(strict=True)
    with path.open('rb') as stream:
        raw = stream.read(262145)
    try:
        value = _json(raw.decode('utf-8'))
        required = {'version', 'instance_id', 'state_dir', 'language', 'model', 'base_url',
                    'permission_policy', 'sources', 'profiles', 'default_profiles', 'system_profiles', 'policy'}
        if (len(raw) > 262144 or not isinstance(value, dict) or required - set(value)
                or set(value) - required - {'interval', 'once_timeout', 'max_workers', 'secrets', 'watchdog', 'execution'}
                or type(value['version']) is not int or value['version'] != 2):
            raise ValueError
        _text(value['instance_id'], 'instance_id', limit=128)
        _text(value['model'], 'model')
        value['base_url'] = api_root(value['base_url'])
        if value['language'] not in ('en', 'zh-CN') or value['permission_policy'] not in ('read_only', 'full_access'):
            raise ValueError
        def resolved(item):
            target = Path(_text(item, 'path', limit=4096)).expanduser()
            target = target if target.is_absolute() else path.parent / target
            if target.is_symlink() or target.is_junction():
                raise ValueError
            return str(target.resolve())
        value['state_dir'] = resolved(value['state_dir'])
        for key, default, maximum in (('interval', .5, 60), ('once_timeout', 600, 86400), ('max_workers', 32, 256)):
            item = value.setdefault(key, default)
            if (isinstance(item, bool) or not isinstance(item, (int, float)) or not 0 < item <= maximum
                    or key == 'max_workers' and type(item) is not int
                    or key == 'interval' and item < .01 or key == 'once_timeout' and item < 1):
                raise ValueError
        from .superagent_watchdog import watchdog_limits
        value['watchdog'] = watchdog_limits(value.get('watchdog'))
        kinds = {'local': LocalSourceKind(), 'github': GitHubSourceKind(), 'feishu': FeishuSourceKind()}
        if not isinstance(value['sources'], dict) or not 1 <= len(value['sources']) <= 32:
            raise ValueError
        for source_id, source in value['sources'].items():
            _text(source_id, 'source_id', limit=128)
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', source_id):
                raise ValueError
            if (not isinstance(source, dict) or set(source) - {'kind', 'config', 'enabled', 'actors', 'threads', 'transport'}
                    or not {'kind', 'config'} <= set(source) or source['kind'] not in kinds):
                raise ValueError
            source['config'] = kinds[source['kind']].validate_config(source['config'])
            if type(source.setdefault('enabled', True)) is not bool:
                raise ValueError
            if source['kind'] == 'local':
                source['actors'] = _names(source.get('actors'), 'actors', nonempty=True)
                source['threads'] = _names(source.get('threads'), 'threads', nonempty=True)
                if 'transport' in source:
                    raise ValueError
            else:
                if {'actors', 'threads'} & set(source):
                    raise ValueError
                transport = source.get('transport')
                if (not isinstance(transport, dict) or set(transport) - {'api_url', 'credential_ref', 'webhook_port'}
                        or 'webhook_port' not in transport or type(transport['webhook_port']) is not int
                        or not 0 <= transport['webhook_port'] <= 65535):
                    raise ValueError
                transport['api_url'] = api_root(transport.get('api_url', 'https://api.github.com' if source['kind'] == 'github' else 'https://open.feishu.cn'))
                if 'credential_ref' in transport and not re.fullmatch(r'secret://[A-Za-z0-9_-]{1,128}', transport['credential_ref']):
                    raise ValueError
        ports = [s['transport']['webhook_port'] for s in value['sources'].values() if 'transport' in s and s['transport']['webhook_port']]
        if len(ports) != len(set(ports)):
            raise ValueError
        if not isinstance(value['profiles'], dict) or not 1 <= len(value['profiles']) <= 32:
            raise ValueError
        for profile_id, profile in value['profiles'].items():
            required_profile = {'repo', 'description', 'source_instance_ids', 'actions', 'tools'}
            if not isinstance(profile, dict) or required_profile - set(profile) or set(profile) - required_profile - {'model', 'max_turns', 'max_seconds', 'max_work_turns'}:
                raise ValueError
            profile['repo'] = resolved(profile['repo'])
            for key in ('source_instance_ids', 'actions', 'tools'):
                profile[key] = _names(profile[key], key, nonempty=key != 'tools')
            parsed = WorkerProfile(profile_id=profile_id, **profile)
            if not set(parsed.source_instance_ids) <= set(value['sources']):
                raise ValueError
        if not isinstance(value['default_profiles'], dict) or set(value['default_profiles']) != set(value['sources']):
            raise ValueError
        for source_id, profile_id in value['default_profiles'].items():
            if profile_id not in value['profiles'] or source_id not in value['profiles'][profile_id]['source_instance_ids']:
                raise ValueError
        system = value['system_profiles']
        if (not isinstance(system, dict) or set(system) != {'integrate', 'review'}
                or len(set(system.values())) != 2 or not set(system.values()) <= set(value['profiles'])
                or set(system.values()) & set(value['default_profiles'].values())):
            raise ValueError
        value['policy'] = validate_policy(value['policy'])
        if set(value['policy']['sources']) != set(value['sources']):
            raise ValueError
        for part in value['policy']['sources'].values():
            threads = (part.get('scope') or {}).get('thread_ids', [])
            if '$current' in threads and threads != ['$current']:
                raise ValueError
        secrets = value.setdefault('secrets', {})
        if not isinstance(secrets, dict) or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key)
                or not isinstance(env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', env) for key, env in secrets.items()):
            raise ValueError
        if 'execution' in value:
            from .superagent_remote_protocol import execution_config
            value['execution']=execution_config(value['execution'],secrets)
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise _error('invalid', field='config') from exc


def config_identity(spec):
    """凭据值不进入账本；完整当前配置固定实例身份。"""
    return {'config': spec}


def local_identity(spec, event):
    """仅本机显式配置的身份与 Thread 可由可信入口提交。"""
    metadata = event.get('metadata', {})
    source = spec['sources'].get(metadata.get('source_instance_id'))
    if (source is None or source['kind'] != 'local'
            or metadata.get('actor_id') not in source['actors'] or event.get('thread_id') not in source['threads']):
        raise _error('source-unavailable')
    return {'actor_id': metadata['actor_id'], 'actor_kind': 'human', 'addressed': 'direct'}
