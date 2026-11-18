"""Capture -> parse -> publish, with rule pinning and layout isolation.

Ordering guarantees:

* the raw page is archived before anything is parsed, so a quarantined or
  unsupported page still leaves evidence behind;
* one item (url + observation) is pinned to one approved rule version at
  the first decision; retries, in-flight captures and historical reparses
  reuse that pin, so an item never oscillates between old and new rules;
* a required-field anomaly isolates only that layout - other approved
  layouts keep producing entries while raw pages of the isolated layout
  keep being captured;
* nothing is ever parsed by a rule that was not approved for the page's
  detected layout.
"""

from urllib.request import urlopen

from .rules import RuleError, get_rule, nature
from .trial import page_layout


ACCEPTED = 'accepted'
QUARANTINED = 'quarantined'
ISOLATED = 'isolated'
WITHDRAWN = 'withdrawn_rule'
UNSUPPORTED = 'unsupported_layout'
REJECTED = 'rejected_page'

NON_ARTICLE = ('login_page', 'empty_shell', 'not_html', 'undecodable', 'http_error')


class Collector:
    def __init__(self, archive, catalog, registry):
        self.archive = archive
        self.catalog = catalog
        self.registry = registry
        registry.db.execute(
            'CREATE TABLE IF NOT EXISTS ingest_pins('
            'url TEXT NOT NULL, observed INTEGER NOT NULL, layout TEXT NOT NULL, '
            'version_id INTEGER NOT NULL, scope_prefix TEXT, '
            'PRIMARY KEY(url, observed))')
        registry.db.commit()

    # -- capture ---------------------------------------------------------

    def fetch(self, url, observed, capture_key=None, timeout=5):
        with urlopen(url, timeout=timeout) as response:
            body = response.read()
            status = response.status
            content_type = response.headers.get('Content-Type', '')
        return self.archive.capture(url, observed, status, content_type, body,
                                    capture_key=capture_key)

    def collect(self, url, observed, capture_key=None):
        return self.ingest(self.fetch(url, observed, capture_key))

    # -- parse -----------------------------------------------------------

    def _pin(self, url, observed):
        row = self.registry.db.execute(
            'SELECT version_id, scope_prefix FROM ingest_pins WHERE url=? AND observed=?',
            (url, observed)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def _set_pin(self, url, observed, layout, version_id, prefix):
        with self.registry.db:
            self.registry.db.execute(
                'INSERT OR IGNORE INTO ingest_pins(url, observed, layout, version_id, scope_prefix) '
                'VALUES(?,?,?,?,?)', (url, observed, layout, version_id, prefix))

    def _outcome(self, page, layout, status, detail, *, version_id=None, rule=None,
                 missing=None, article=None):
        return {'page_id': page['id'], 'url': page['url'], 'observed': page['observed'],
                'digest': page['digest'], 'layout': layout, 'version_id': version_id,
                'rule': rule, 'status': status, 'detail': detail,
                'missing': list(missing or ()), 'article': article}

    def _attempt(self, page, layout, version_id, rule, outcome, missing=(), detail=''):
        with self.registry.db:
            self.registry.db.execute(
                'INSERT INTO parse_attempts(page_id,url,observed,layout,version_id,rule,'
                'outcome,missing,detail) VALUES(?,?,?,?,?,?,?,?,?)',
                (page['id'], page['url'], page['observed'], layout, version_id, rule,
                 outcome, ','.join(missing), detail))

    def ingest(self, page_id):
        """Parse one already archived page.  Returns a traceable outcome."""
        page = self.archive.get(page_id)
        url = page['url']
        observed = page['observed']
        layout = page_layout(page)
        if page.get('layout') != layout and layout is not None:
            self.archive.set_layout(page_id, layout)

        if page['status'] != 200:
            detail = 'http status %s' % page['status']
            self._attempt(page, layout, None, None, REJECTED, detail=detail)
            return self._outcome(page, layout, REJECTED, detail)

        problem = nature(page)
        if problem is not None:
            self._attempt(page, layout, None, None, REJECTED, detail=problem)
            return self._outcome(page, layout, REJECTED, problem)

        if layout is None:
            detail = 'page does not announce a known layout'
            self._attempt(page, None, None, None, UNSUPPORTED, detail=detail)
            return self._outcome(page, None, UNSUPPORTED, detail)

        version_id, prefix = self._pin(url, observed)
        if version_id is None:
            version = self.registry.active(url, layout)
            if version is None:
                isolation = self.registry.isolation_for(url, layout)
                if isolation is not None:
                    status, detail = ISOLATED, 'scope is isolated: ' + _describe(isolation)
                elif self.registry.withdrawn_coverage(url, layout):
                    status = WITHDRAWN
                    detail = 'the approval covering this url and layout was withdrawn'
                else:
                    status = UNSUPPORTED
                    detail = 'no approved scope for this url and layout'
                self._attempt(page, layout, None, None, status, detail=detail)
                return self._outcome(page, layout, status, detail)
            version_id = version['id']
            prefix = version.get('_prefix')
            self._set_pin(url, observed, layout, version_id, prefix)
        version = self.registry.version(version_id)
        if version['withdrawn_at']:
            detail = 'pinned rule version %s was withdrawn' % version_id
            self._attempt(page, layout, version_id, version['rule'], WITHDRAWN, detail=detail)
            return self._outcome(page, layout, WITHDRAWN, detail,
                                 version_id=version_id, rule=version['rule'])

        # An isolated scope stays closed for new decisions, including items
        # pinned before the anomaly was noticed.  Isolation dominates every
        # approval covering this url/layout.
        isolation = self.registry.isolation_for(url, layout)
        if isolation is not None:
            detail = 'scope is isolated: ' + _describe(isolation)
            self._attempt(page, layout, version_id, version['rule'], ISOLATED, detail=detail)
            return self._outcome(page, layout, ISOLATED, detail,
                                 version_id=version_id, rule=version['rule'])

        rule = get_rule(version['rule'])

        try:
            fields = rule.parse(page)
        except RuleError as error:
            return self._fail(page, layout, prefix, version, rule, error.code, str(error))

        missing = [field for field in version['fields'].split(',') if not fields.get(field)]
        if missing:
            return self._fail(page, layout, prefix, version, rule, 'missing_required_fields',
                              'missing ' + ','.join(missing), missing)

        article = {'url': url, 'page_id': page_id, 'observed': observed,
                   'digest': page['digest'], 'layout': layout,
                   'rule': rule.name, 'rule_version': rule.version,
                   'version_id': version_id, 'fields': fields, **fields}
        self._attempt(page, layout, version_id, rule.name, ACCEPTED)
        published = self.catalog.publish(article)
        return self._outcome(page, layout, ACCEPTED, '', version_id=version_id,
                             rule=rule.name, article=article) | {'published': published}

    def _fail(self, page, layout, prefix, version, rule, code, detail, missing=()):
        """Isolate exactly the scope that produced an anomaly."""
        if code in NON_ARTICLE:
            status = REJECTED
        else:
            self.registry.isolate_scope(layout, prefix, page['url'])
            status = QUARANTINED
        self._attempt(page, layout, version['id'], rule.name, status, missing, detail)
        return self._outcome(page, layout, status, detail, version_id=version['id'],
                             rule=rule.name, missing=missing)

    # -- reparse ---------------------------------------------------------

    def reparse(self, page_id):
        """Reparse an archived page using the same pinned version.

        A page that was never decided (for example captured before approval)
        is pinned now to the currently approved scope.  Published entries
        with the same observation are not replaced, so a historical reparse
        cannot roll an item back to an older rule.
        """
        return self.ingest(page_id)


def _describe(isolation):
    if isolation.get('scope') == 'prefix':
        return 'prefix ' + isolation['prefix']
    if isolation.get('scope') == 'url':
        return 'url ' + isolation['url']
    return 'layout ' + isolation['layout']


def collect(url, observed, archive, extractor, catalog):
    """Legacy entry point: fetch, archive, parse with one explicit rule."""
    from .review import Review
    with urlopen(url, timeout=5) as response:
        page_id = archive.capture(url, observed, response.status,
                                  response.headers.get('Content-Type', ''), response.read())
    page = archive.get(page_id)
    article = extractor.parse(page)
    assessment = Review().assess(page, article)
    if not assessment['accepted']:
        raise ValueError('article fields are incomplete: ' + ','.join(assessment['missing']))
    catalog.publish(article)
    return article
