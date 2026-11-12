"""飞书消息的文本和资源引用解析；不访问网络或把文件名解释为路径。"""
from __future__ import annotations

import re

from .superagent_common import _text


def feishu_content(kind, content, *, bot_id=''):
    if not isinstance(content,dict):
        raise ValueError('media/content')
    attachments = []
    def resource(key, media_type, name=''):
        _text(key,'resource_key',limit=256)
        if not re.fullmatch(r'(img|file)_[A-Za-z0-9_-]{1,250}',key):
            raise ValueError('media/key')
        _text(name,'file_name',limit=256,empty=True)
        item = {'resource_key':key,'type':media_type,'name':name}
        if item not in attachments:
            attachments.append(item)
        if len(attachments)>16:
            raise ValueError('media/capacity')
    mentioned = False
    if kind == 'text':
        return {'text':_text(content.get('text'),'text',limit=65536,empty=True)},False
    if kind == 'image':
        resource(content.get('image_key'),'image')
        text = ''
    elif kind in {'file','audio','media'}:
        resource(content.get('file_key'),kind,content.get('file_name',''))
        if kind == 'media' and content.get('image_key'):
            resource(content['image_key'],'image')
        text = ''
    elif kind == 'post':
        if 'content' not in content:
            supported = [value for language,value in content.items() if language in {'zh_cn','en_us','ja_jp'}]
            if len(supported)!=1:
                raise ValueError('media/post-locale')
            content = supported[0]
        if not isinstance(content,dict) or not isinstance(content.get('content'),list) or len(content['content'])>256:
            raise ValueError('media/post')
        lines = [_text(content.get('title',''),'title',limit=4096,empty=True)]
        first = True
        for line in content['content']:
            if not isinstance(line,list) or len(line)>256:
                raise ValueError('media/post-line')
            pieces = []
            for item in line:
                if not isinstance(item,dict):
                    raise ValueError('media/post-node')
                tag = item.get('tag')
                if tag in {'text','a','md','code_block'}:
                    value = _text(item.get('text',''),'text',limit=65536,empty=True)
                    pieces.append(value)
                    if value.strip():
                        first = False
                elif tag == 'at':
                    identifier = _text(item.get('user_id'),'user_id',limit=128)
                    mentioned = mentioned or first and identifier == bot_id
                    pieces.append('@'+identifier)
                    first = False
                elif tag == 'img':
                    resource(item.get('image_key'),'image')
                    first = False
                elif tag == 'media':
                    resource(item.get('file_key'),'media')
                    first = False
                elif tag not in {'hr','emotion'}:
                    raise ValueError('media/post-tag')
            lines.append(''.join(pieces))
        text = _text('\n'.join(lines).strip(),'text',limit=65536,empty=True)
    else:
        raise ValueError('media/type')
    return {'text':text,'attachments':attachments},mentioned
