"""从模型文本记录中移除流式终端控制序列。

浏览器仍接收完整的原始 VT 流。此处生成文本记录，不额外模拟屏幕，
因此忽略光标移动和标题更新。
"""


class TerminalTranscript:
    def __init__(self):
        self.state = 'text'

    def feed(self, text: str) -> str:
        out = []
        for char in text:
            if self.state == 'text':
                if char == '\x1b':
                    self.state = 'escape'
                elif char == '\x9b':
                    self.state = 'csi'
                elif char in '\x90\x9d\x9e\x9f':
                    self.state = 'string'
                elif (ord(char) >= 32 and not 0x7f <= ord(char) <= 0x9f) or char in '\n\r\t\b':
                    out.append(char)
            elif self.state == 'escape':
                if char == '[':
                    self.state = 'csi'
                elif char in ']P^_':
                    self.state = 'string'
                elif char == '\x1b' or ' ' <= char <= '/':
                    continue
                else:
                    self.state = 'text'
            elif self.state == 'csi':
                if '@' <= char <= '~':
                    self.state = 'text'
                elif char == '\x1b':
                    self.state = 'escape'
            elif self.state == 'string':
                if char in '\x07\x9c':
                    self.state = 'text'
                elif char == '\x1b':
                    self.state = 'string-escape'
            elif self.state == 'string-escape':
                if char == '\\':
                    self.state = 'text'
                elif char != '\x1b':
                    self.state = 'string'
        return ''.join(out)
