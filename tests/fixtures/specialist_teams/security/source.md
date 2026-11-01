# 构造的安全审查案例
这是离线源码片段，不对应真实目标，禁止执行利用。
已知：GET /download 的 name 参数是调用者可控字符串，没有上游过滤；攻击者是已登录的普通租户用户。授权应仅允许读取 /srv/tenant-a 目录内文件。/srv/tenant-ab/private.txt 实际存在且服务进程有读权限。没有符号链接。
```python
from pathlib import Path
ROOT = Path('/srv/tenant-a')
def download(name):
    target = (ROOT / name).resolve()
    if not str(target).startswith(str(ROOT)):
        raise PermissionError('outside root')
    return target.read_text(encoding='utf-8')
```
name='../tenant-ab/private.txt' 时 resolve 得到 /srv/tenant-ab/private.txt。
该字符串以 /srv/tenant-a 开头。
只给出了本片段，没有生产日志、其他路由和实际攻击记录。
