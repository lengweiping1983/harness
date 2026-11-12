"""交互子系统：把「向用户提问」做成可挂起 / 可恢复的能力缝。"""

from .questions import (
    DEFAULT_ASK_TIMEOUT,
    MAX_ASK_TIMEOUT,
    MAX_QUESTIONS,
    QuestionBroker,
    QuestionError,
    UserQuestion,
    normalize_answers,
)

__all__ = [
    "QuestionBroker",
    "QuestionError",
    "UserQuestion",
    "normalize_answers",
    "DEFAULT_ASK_TIMEOUT",
    "MAX_ASK_TIMEOUT",
    "MAX_QUESTIONS",
]
