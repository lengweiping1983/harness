"""共享协作的产品消息独立于稳定的机器错误码。"""
from ..core.language import current_language
from ..i18n import resources, tr


def collaboration_error_message(code: str) -> str:
    """仅根据公开机器码展示消息，不转发私有异常正文。"""
    key = 'ui.collaboration.error.' + code
    if key in resources(current_language.get()):
        return tr(key, code=code)
    return tr('ui.collaboration.operation_failed', code=code)
