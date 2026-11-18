import hashlib
import sqlite3


class PageArchive:
    """Append-only store of raw pages.

    Every capture is kept and evidence is never overwritten by a later
    capture.  ``capture_key`` identifies one capture attempt (for example
    the collector's operation id): repeating it returns the original page id
    so a retried fetch cannot be mistaken for a new page version.  Without a
    capture key each call appends a new version, as before.
    """

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS pages(
            id INTEGER PRIMARY KEY, url TEXT, observed INTEGER, status INTEGER,
            content_type TEXT, digest TEXT, body BLOB, layout TEXT, capture_key TEXT)''')
        self._migrate()
        self.db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS pages_capture_identity
            ON pages(url, capture_key) WHERE capture_key IS NOT NULL''')
        self.db.commit()

    def _migrate(self):
        existing = {row[1] for row in self.db.execute('PRAGMA table_info(pages)')}
        for name in ('layout', 'capture_key'):
            if name not in existing:
                self.db.execute(f'ALTER TABLE pages ADD COLUMN {name} TEXT')

    def capture(self, url, observed, status, content_type, body, *,
                layout=None, capture_key=None):
        if not isinstance(body, bytes):
            raise TypeError('raw page must be bytes')
        if capture_key is not None:
            row = self.db.execute(
                'SELECT id FROM pages WHERE url=? AND capture_key=?',
                (url, capture_key)).fetchone()
            if row is not None:
                return row[0]
        with self.db:
            cursor = self.db.execute(
                'INSERT INTO pages(url,observed,status,content_type,digest,body,layout,capture_key) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (url, observed, status, content_type,
                 hashlib.sha256(body).hexdigest(), body, layout, capture_key))
        return cursor.lastrowid

    def set_layout(self, page_id, layout):
        with self.db:
            self.db.execute('UPDATE pages SET layout=? WHERE id=?', (layout, page_id))

    def get(self, page_id):
        row = self.db.execute('SELECT * FROM pages WHERE id=?', (page_id,)).fetchone()
        if row is None:
            raise KeyError(page_id)
        return dict(row)

    def observations(self, url):
        rows = self.db.execute(
            'SELECT * FROM pages WHERE url=? ORDER BY observed DESC, id DESC', (url,)).fetchall()
        return [dict(row) for row in rows]
