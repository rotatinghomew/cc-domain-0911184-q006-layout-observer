from html.parser import HTMLParser


class LegacyArticle(HTMLParser):
    """Original low-level parser kept for the legacy single-rule entry point."""

    def __init__(self):
        super().__init__()
        self.fields = {}
        self.current = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.current:
            self.depth += 1
        elif attributes.get('data-field') in ('title', 'author', 'license', 'published'):
            self.current = attributes['data-field']
            self.fields.setdefault(self.current, '')
            self.depth = 1

    def handle_endtag(self, tag):
        if self.current:
            self.depth -= 1
            if self.depth == 0:
                self.current = None

    def handle_data(self, data):
        if self.current:
            self.fields[self.current] += data


class Extractor:
    """Legacy ``data-field`` parser; new code should use :mod:`observer.rules`."""

    def parse(self, page):
        if page['status'] != 200 or not page['content_type'].startswith('text/html'):
            raise ValueError('not an HTML success response')
        parser = LegacyArticle()
        parser.feed(page['body'].decode('utf-8'))
        fields = {key: value.strip() for key, value in parser.fields.items()}
        if not fields.get('title'):
            raise ValueError('no article title')
        return {'url': page['url'], 'page_id': page['id'], 'observed': page['observed'],
                'rule': 'legacy-v1', **fields}
