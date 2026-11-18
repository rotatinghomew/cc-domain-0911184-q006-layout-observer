import hashlib
import sqlite3


class PageArchive:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS pages(
            id INTEGER PRIMARY KEY, url TEXT, observed INTEGER, status INTEGER,
            content_type TEXT, digest TEXT, body BLOB)''')

    def capture(self, url, observed, status, content_type, body):
        if not isinstance(body, bytes):
            raise TypeError('raw page must be bytes')
        with self.db:
            cursor = self.db.execute('INSERT INTO pages(url,observed,status,content_type,digest,body) VALUES(?,?,?,?,?,?)',
                                     (url, observed, status, content_type, hashlib.sha256(body).hexdigest(), body))
        return cursor.lastrowid

    def get(self, page_id):
        row = self.db.execute('SELECT * FROM pages WHERE id=?', (page_id,)).fetchone()
        if row is None:
            raise KeyError(page_id)
        return dict(row)
