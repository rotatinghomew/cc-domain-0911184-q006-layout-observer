import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PAGES = {
    '/east/legacy': '<article data-layout="legacy"><h1 data-field="title">灯火</h1><p data-field="author">合作社甲</p><p data-field="license">区域发行</p><time data-field="published">2026-11-10</time></article>',
    '/east/folded': '<article data-layout="folded"><h1>灯火新版</h1><details><dl><dt>作者</dt><dd>合作社乙</dd><dt>授权</dt><dd>网络发行</dd></dl><time datetime="2026-11-11"></time></details></article>',
    '/west/legacy': '<article data-layout="legacy"><h1 data-field="title">归途</h1><p data-field="author">合作社丙</p><p data-field="license">西部发行</p><time data-field="published">2026-11-09</time></article>',
    '/login': '<title>登录</title><form><input name="password" type="password"></form>',
    '/shell': '<article data-layout="legacy"><h1 data-field="title">正在加载</h1><div id="client-root"></div></article>',
}


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
