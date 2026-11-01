"""原生页面生命周期，与发出的浏览器操作分开记录。"""


class BrowserNavigation:
    def __init__(self):
        self.phases = {}
        self.requests = {}
        self.documents = {}

    @staticmethod
    def main_request(page, request):
        try:
            return request.is_navigation_request() and request.frame == page.main_frame
        except (AttributeError, RuntimeError):
            return False

    def requested(self, page, request):
        if self.main_request(page, request):
            self.requests[page] = request
            self.phases[page] = 'requesting'

    def committed(self, page, frame):
        if frame != page.main_frame:
            return False
        self.documents[page] = self.documents.get(page, 0) + 1
        # 片段或历史状态变化不会触发新的资源加载生命周期。
        if self.phases.get(page) == 'requesting':
            self.phases[page] = 'loading'
        return True

    def ready(self, page, phase):
        if self.phases.get(page) not in {'requesting', 'stopped', 'failed'}:
            self.phases[page] = phase

    def failed(self, page, request):
        if self.requests.get(page) is request and self.phases.get(page) != 'stopped':
            self.phases[page] = 'failed'

    def view(self, page):
        return {'phase': self.phases.get(page, 'complete')}

    def forget(self, page):
        self.phases.pop(page, None)
        self.requests.pop(page, None)
        self.documents.pop(page, None)
