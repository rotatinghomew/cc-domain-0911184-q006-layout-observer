"""Extraction rules.

A rule turns one archived page into a fixed field set.  It must *attest*
that the page really is the layout it claims: HTTP 200 plus a title is not
enough, because login walls and client-rendered shells also return HTML.
Rules never invent values; a field the layout moved or did not publish is
reported as missing, not as an empty value silently accepted.
"""

from html.parser import HTMLParser


FIELDS = ('title', 'author', 'license', 'published')
LAYOUT_ATTRIBUTE = 'data-layout'
VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
             'link', 'meta', 'param', 'source', 'track', 'wbr'}

LABELS = {
    '标题': 'title',
    '作者': 'author',
    '授权': 'license',
    '授权范围': 'license',
    '发布日期': 'published',
    '发布时间': 'published',
    '时间': 'published',
}

# Successfully served pages that are not article bodies.  These must never
# become normal entries regardless of what URL they appear under.  Detection
# is structural (attributes), not by page copy: an article may contain any
# words, and "正在加载" is not a reliable signal.
SHELL_IDS = {'client-root', 'app-root', '__next'}
SHELL_ATTRIBUTES = {'data-shell': {'empty'}}
LOGIN_HINTS = {'login', 'signin', 'sign-in'}


class RuleError(ValueError):
    """The page could not be extracted by this rule."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def squash(value):
    return ' '.join((value or '').split())


class _Nature(HTMLParser):
    """Structural signals that a 200 page is not an article body."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.password_input = False
        self.form = False
        self.shell = None
        self.login = False

    def handle_starttag(self, tag, attrs):
        attributes = {name: (value or '') for name, value in attrs}
        if tag == 'form':
            self.form = True
        if tag == 'input' and attributes.get('type', '').lower() == 'password':
            self.password_input = True
        for name in ('id', 'class'):
            value = attributes.get(name, '')
            if value in LOGIN_HINTS or any(part in LOGIN_HINTS
                                           for part in value.split()):
                self.login = True
        if attributes.get('id') in SHELL_IDS and self.shell is None:
            self.shell = 'id=' + attributes['id']
        for name, values in SHELL_ATTRIBUTES.items():
            if attributes.get(name) in values and self.shell is None:
                self.shell = '%s=%s' % (name, attributes[name])

    handle_startendtag = handle_starttag


def nature(page):
    """Classify non-article pages without trusting page copy.

    Returns a reason code, or ``None`` when the page may be an article.
    """
    content_type = page.get('content_type') or ''
    if not content_type.startswith('text/html'):
        return 'not_html'
    try:
        text = page['body'].decode('utf-8')
    except UnicodeDecodeError:
        return 'undecodable'
    reader = _Nature()
    reader.feed(text)
    reader.close()
    if reader.password_input and (reader.form or reader.login):
        return 'login_page'
    if reader.shell is not None:
        # Framework wrappers such as <div id="__next"> also appear on fully
        # rendered articles, so the shell marker only decides the page when
        # no more than a title was actually rendered into the HTML.
        fields = _Reader()
        fields.feed(text)
        fields.flush()
        if len([value for value in fields.fields.values() if value]) <= 1:
            return 'empty_shell'
    return None


def preflight(page):
    """Reject login walls and unrendered shells before layout matching."""
    reason = nature(page)
    if reason == 'login_page':
        raise RuleError('login_page', 'page is a login prompt')
    if reason == 'empty_shell':
        raise RuleError('empty_shell', 'page is an unrendered client shell')
    if reason in ('not_html', 'undecodable'):
        raise RuleError(reason, 'page is not usable HTML')


class _Reader(HTMLParser):
    """Collect marked fields plus the structural facts rules may rely on."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields = {}
        self.layout = None
        self.headings = []
        self.times = []
        self.pairs = []
        self._last_dt = None
        self._stack = []

    def _open(self, tag, kind, name=None):
        entry = {'tag': tag, 'kind': kind, 'name': name, 'chunks': []}
        if tag in VOID_TAGS:
            self._finish(entry)
        else:
            self._stack.append(entry)

    def _finish(self, entry):
        text = squash(''.join(entry['chunks']))
        kind = entry['kind']
        if kind == 'field':
            self.fields.setdefault(entry['name'], text)
        elif kind == 'h1':
            self.headings.append(text)
        elif kind == 'dt':
            self._last_dt = text
        elif kind == 'dd':
            self.pairs.append((self._last_dt, text))

    def handle_starttag(self, tag, attrs):
        attributes = {name: (value or '') for name, value in attrs}
        if self.layout is None and attributes.get(LAYOUT_ATTRIBUTE):
            self.layout = attributes[LAYOUT_ATTRIBUTE]
        if tag == 'time' and attributes.get('datetime'):
            self.times.append(attributes['datetime'].strip())
        name = attributes.get('data-field') or attributes.get('data-capture')
        if name in FIELDS:
            self._open(tag, 'field', name)
        elif tag in ('h1', 'dt', 'dd'):
            self._open(tag, tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]['tag'] == tag:
                for entry in self._stack[index:][::-1]:
                    self._finish(entry)
                del self._stack[index:]
                return

    def handle_data(self, data):
        for entry in self._stack:
            entry['chunks'].append(data)

    def flush(self):
        for entry in self._stack[::-1]:
            self._finish(entry)
        self._stack.clear()


def inspect(page):
    """Check transport and page nature, then read structure.

    Returns ``(reader, layout)`` where layout may be ``None`` when the page
    carries no layout marker (an uncovered layout, never a default).
    """
    if page['status'] != 200:
        raise RuleError('http_error', 'page was not served successfully')
    preflight(page)
    text = page['body'].decode('utf-8')
    reader = _Reader()
    reader.feed(text)
    reader.flush()
    return reader, reader.layout


def detect_layout(page):
    """Layout announced by the page, independent of page nature.

    A shell may still carry its layout marker; that is useful for reporting
    and for isolating the right scope, while :func:`nature` decides whether
    it may ever be published.
    """
    if page.get('layout'):
        return page['layout']
    try:
        content_type = page.get('content_type') or ''
        if not content_type.startswith('text/html'):
            return None
        reader = _Reader()
        reader.feed(page['body'].decode('utf-8'))
        reader.flush()
        return reader.layout
    except (UnicodeDecodeError, ValueError):
        return None


class Rule:
    name = ''
    layout = ''
    version = 1

    def ident(self):
        return f'{self.name}@{self.version}'

    def scope(self):
        return {'layout': self.layout, 'fields': list(FIELDS)}

    def parse(self, page):
        reader, layout = inspect(page)
        self.attest(reader, layout)
        fields = self.collect(reader)
        if not fields.get('title'):
            raise RuleError('no_article', 'no article title in ' + self.layout + ' layout')
        return fields

    def attest(self, reader, layout):
        raise NotImplementedError

    def collect(self, reader):
        return {key: value for key, value in reader.fields.items() if value}

    def _attest_layout(self, layout):
        if layout != self.layout:
            raise RuleError('unsupported_layout',
                            'page is not the %s layout (saw %r)' % (self.layout, layout))


class LegacyRule(Rule):
    """``data-field`` markers placed beside the article body."""

    name = 'legacy'
    layout = 'legacy'

    def attest(self, reader, layout):
        self._attest_layout(layout)


class FoldedRule(Rule):
    """Title outside ``<details>``; author/license/published collapsed inside."""

    name = 'folded'
    layout = 'folded'

    def attest(self, reader, layout):
        self._attest_layout(layout)
        if not reader.pairs and not any(name in reader.fields for name in FIELDS):
            raise RuleError('no_article', 'folded layout has no collapsed field list')

    def collect(self, reader):
        fields = {key: value for key, value in reader.fields.items() if value}
        if reader.headings and reader.headings[0]:
            fields.setdefault('title', reader.headings[0])
        for label, value in reader.pairs:
            field = LABELS.get(label)
            if field and value:
                fields.setdefault(field, value)
        if reader.times:
            fields.setdefault('published', reader.times[0])
        return fields


class MarkedRule(Rule):
    """Explicit ``data-capture`` markers used by local test pages."""

    name = 'marked'
    layout = 'marked'

    def attest(self, reader, layout):
        self._attest_layout(layout)


REGISTRY = {rule.name: rule for rule in (LegacyRule(), FoldedRule(), MarkedRule())}


def get_rule(name):
    try:
        return REGISTRY[name]
    except KeyError:
        raise RuleError('unsupported_layout', 'unknown rule %r' % (name,)) from None


class Extractor:
    """Parse one page with one named rule (defaults to the legacy layout)."""

    def __init__(self, rule='legacy'):
        self.rule = get_rule(rule)

    def parse(self, page):
        fields = self.rule.parse(page)
        return {'url': page['url'], 'page_id': page['id'], 'observed': page['observed'],
                'rule': self.rule.name, 'rule_version': self.rule.version, **fields}
