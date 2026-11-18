import json
import sqlite3


class Catalog:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS articles(url TEXT PRIMARY KEY, observed INTEGER, payload TEXT)')

    def publish(self, article):
        with self.db:
            old = self.db.execute('SELECT observed FROM articles WHERE url=?', (article['url'],)).fetchone()
            if old is None or article['observed'] > old[0]:
                self.db.execute('INSERT OR REPLACE INTO articles VALUES(?,?,?)',
                                (article['url'], article['observed'], json.dumps(article, ensure_ascii=False)))

    def rows(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM articles ORDER BY url')]
