"""真人主体标识的公共格式校验，供接入身份及运行存储共同使用。"""
import re

from .errors import HarnessError
from ..i18n import tr


def validate_subject(value):
    """主体标识只验证一次合同，凭据与成员账本共用。"""
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_.@-]{0,127}', value):
        raise HarnessError(tr('ui.collaboration.error.access/invalid-subject'), code='access/invalid-subject')
    return value


