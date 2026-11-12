"""HTTP 写入准入与公开契约共享的字段边界。"""
from .attachments import MAX_BATCH_FILES

TITLE_MAX_LENGTH = 512
TITLE = {'type': ['string', 'null'], 'maxLength': TITLE_MAX_LENGTH}
SESSION_PATCH = {'type': 'object', 'additionalProperties': False, 'properties': {
    'title': TITLE, 'model': {'type': ['string', 'null']},
    'reasoning_effort': {'type': ['string', 'null']}, 'reasoning_effort_inherit': {'type': 'boolean'}}}
UPLOAD_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['files'], 'properties': {
    'files': {'type': 'array', 'minItems': 1, 'maxItems': MAX_BATCH_FILES, 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['name', 'data'], 'properties': {
            'name': {'type': 'string', 'minLength': 1, 'maxLength': 255},
            'mediaType': {'type': 'string', 'maxLength': 255}, 'data': {'type': 'string'}}}}}}
