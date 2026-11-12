"""任务板对外消息与内部机器错误码分离。"""
from ..core.language import current_language
from ..i18n import resources, tr


def board_error_message(code: str) -> str:
    """保留诊断原文和机器码，只翻译已知的产品消息。"""
    key = 'project.board.error.' + code.removeprefix('board/')
    if code.startswith('board/') and key in resources(current_language.get()):
        return tr(key, code=code)
    return tr('project.board.error.unknown', detail=code)
