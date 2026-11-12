"""提取可持久化的有界故障字段，不保存供应商正文或凭据。"""


def failure_diagnostics(error):
    """只接收固定枚举和数字，避免错误详情夹带任意远程文本。"""
    details = getattr(error, 'details', {})
    result = {}
    phase = details.get('phase')
    if phase in {'connect', 'read', 'network', 'progress', 'generation'}:
        result['phase'] = phase
    name = details.get('network_error')
    if name in {'BrokenPipeError', 'ConnectionResetError', 'ConnectionAbortedError',
                'ConnectionRefusedError', 'TimeoutError', 'SSLError', 'SSLCertVerificationError',
                'gaierror', 'IncompleteRead', 'RemoteDisconnected', 'OSError'}:
        result['network_error'] = name
    for key in ('errno', 'status'):
        value = details.get(key)
        if type(value) is int and -65536 <= value <= 65536:
            result[key] = value
    for key in ('runtime_exit_code', 'response_bytes'):
        value = details.get(key)
        minimum = -65536 if key == 'runtime_exit_code' else 0
        if type(value) is int and minimum <= value <= 2**53 - 1:
            result[key] = value
    for key in ('seconds', 'retry_after'):
        value = details.get(key)
        if type(value) in (int, float) and 0 <= value <= 86400:
            result[key] = value
    if isinstance(getattr(error, 'transient', None), bool):
        result['transient'] = error.transient
    return result
