import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


LEGACY = ('<article data-layout="legacy"><h1 data-field="title">{title}</h1>'
          '<p data-field="author">{author}</p><p data-field="license">{license}</p>'
          '<time data-field="published">{published}</time></article>')
FOLDED = ('<article data-layout="folded"><h1>{title}</h1><details><summary>详情</summary><dl>'
          '<dt>作者</dt><dd>{author}</dd>'
          '<dt>授权范围</dt><dd>{license}</dd>'
          '<dt>发布时间</dt><dd>{published}</dd>'
          '</dl></details></article>')
MARKED = ('<article data-layout="marked"><h1 data-capture="title">{title}</h1>'
          '<p data-capture="author">{author}</p><p data-capture="license">{license}</p>'
          '<p data-capture="published">{published}</p></article>')

PAGES = {
    '/east/legacy': LEGACY.format(title='灯火', author='合作社甲',
                                  license='区域发行', published='2026-11-10'),
    '/east/folded': FOLDED.format(title='灯火新版', author='合作社乙',
                                  license='网络发行', published='2026-11-11'),
    '/west/legacy': LEGACY.format(title='归途', author='合作社丙',
                                  license='西部发行', published='2026-11-09'),
    '/west/folded': FOLDED.format(title='归途新版', author='合作社丁',
                                  license='西部发行', published='2026-11-12'),
    '/login': '<title>登录</title><form><input name="password" type="password"></form>',
    '/shell': '<article data-layout="legacy"><h1 data-field="title">正在加载</h1><div id="client-root"></div></article>',
    '/unmarked': '<article><h1>无标记</h1><p>未知版式</p></article>',
    '/east/fresh': FOLDED.format(title='新稿', author='合作社戊',
                                 license='全国发行', published='2026-11-14'),
}

# A folded page whose license block was moved out of the collapsed area by a
# partner update.  The page still returns 200 but the approved rule must not
# silently publish an empty license.
BROKEN_FOLDED = ('<article data-layout="folded"><h1>灯火新版</h1><details><summary>详情</summary><dl>'
                 '<dt>作者</dt><dd>合作社乙</dd></dl></details></article>')


@contextmanager
def serve(pages=None):
    pages = dict(PAGES) if pages is None else pages

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in pages:
                self.send_error(404)
                return
            data = pages[self.path].encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', pages
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
