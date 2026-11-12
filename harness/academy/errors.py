"""Academy 的显式错误身份。"""


class AcademyError(ValueError):
    """保留稳定错误码，由边界按当前语言解析。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
