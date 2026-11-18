"""Trial review.

A confirmation is always bound to one archived page version (its digest).
If the partner updates the page during the trial, the old confirmation no
longer applies; the page is then reported as changed and must be looked at
again.  Text equality with the extracted fields is neither required nor
treated as confirmation.  A layout that no candidate rule can attest is
``unsupported`` - it is never counted as verified.
"""

import json

from .rules import FIELDS, detect_layout, get_rule


CONFIRMED = 'confirmed'
PENDING = 'pending'
CHANGED = 'changed'
MISMATCH = 'mismatch'
REJECTED = 'rejected'
UNSUPPORTED = 'unsupported'
NOT_CAPTURED = 'not_captured'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _unjson(value):
    return json.loads(value)


def counts(statuses):
    result = {}
    for status in statuses:
        result[status] = result.get(status, 0) + 1
    return result


class RuleSet:
    """Candidate rule selection used during trials only."""

    def __init__(self, names):
        self.rules = [get_rule(name) for name in names]

    def try_page(self, page):
        """Return ``(rule_name, fields, error)``; never fabricates success."""
        error = None
        for rule in self.rules:
            try:
                return rule.name, rule.parse(page), None
            except ValueError as caught:
                error = caught
        return None, None, error


def page_layout(page):
    """Layout announced by the page itself, or ``None`` if uncovered."""
    return detect_layout(page)


class Trial:
    def __init__(self, db, archive, candidates):
        self.db = db
        self.archive = archive
        self.candidates = candidates

    # -- selection -------------------------------------------------------

    def add(self, url):
        with self.db:
            self.db.execute(
                'INSERT OR IGNORE INTO trial_items(url, added_at, removed_at) VALUES(?,?,NULL)',
                (url, 0))

    def remove(self, url):
        with self.db:
            self.db.execute('UPDATE trial_items SET removed_at=1 WHERE url=?', (url,))

    def urls(self):
        return [row[0] for row in self.db.execute(
            'SELECT url FROM trial_items WHERE removed_at IS NULL ORDER BY url')]

    # -- evidence --------------------------------------------------------

    def page(self, url):
        observations = self.archive.observations(url)
        return observations[0] if observations else None

    def confirmation(self, url):
        row = self.db.execute(
            'SELECT * FROM confirmations WHERE url=? AND withdrawn_at IS NULL '
            'ORDER BY id DESC LIMIT 1', (url,)).fetchone()
        return dict(row) if row else None

    def confirm(self, page_id, fields, operator, rejected=False):
        """Pin a human decision to exactly one archived page version."""
        page = self.archive.get(page_id)
        values = {}
        for field in FIELDS:
            value = ' '.join((fields.get(field) or '').split())
            if value:
                values[field] = value
        if not rejected and not values.get('title'):
            raise ValueError('confirmation must include a title')
        with self.db:
            self.db.execute(
                'UPDATE confirmations SET withdrawn_at=1 WHERE url=? AND withdrawn_at IS NULL',
                (page['url'],))
            self.db.execute(
                'INSERT INTO confirmations(url, page_id, digest, observed, fields, operator, rejected) '
                'VALUES(?,?,?,?,?,?,?)',
                (page['url'], page_id, page['digest'], page['observed'],
                 _json(values), operator, 1 if rejected else 0))
        return values

    def assess(self, url, proposal_layout=None):
        page = self.page(url)
        if page is None:
            return {'url': url, 'status': NOT_CAPTURED, 'page_id': None, 'digest': None,
                    'observed': None, 'layout': None, 'rule': None, 'candidate': None,
                    'changes': [], 'reason': 'no archived page'}
        confirmation = self.confirmation(url)
        layout = page_layout(page)
        result = {'url': url, 'page_id': page['id'], 'digest': page['digest'],
                  'observed': page['observed'], 'layout': layout,
                  'confirmation_page_id': confirmation['page_id'] if confirmation else None,
                  'confirmation_digest': confirmation['digest'] if confirmation else None,
                  'rule': None, 'candidate': None, 'changes': [], 'reason': None}
        if layout is None:
            result['status'] = UNSUPPORTED
            result['reason'] = 'page does not announce a known layout'
            return result
        if proposal_layout is not None and layout != proposal_layout:
            result['status'] = UNSUPPORTED
            result['reason'] = 'layout %r is outside the proposed scope' % (layout,)
            return result
        name, candidate, error = self.candidates.try_page(page)
        result['rule'] = name
        if candidate is None:
            result['status'] = UNSUPPORTED
            result['reason'] = error.code if error is not None else 'no candidate rule matched'
            return result
        result['candidate'] = candidate
        if confirmation is None:
            result['status'] = PENDING
            return result
        if confirmation['digest'] != page['digest']:
            result['status'] = CHANGED
            result['reason'] = 'page version differs from the confirmed version'
            return result
        if confirmation.get('rejected'):
            result['status'] = REJECTED
            return result
        expected = _unjson(confirmation['fields'])
        for field in FIELDS:
            want = expected.get(field)
            got = candidate.get(field)
            if want != got:
                result['changes'].append({'field': field, 'confirmed': want, 'candidate': got})
        result['status'] = CONFIRMED if not result['changes'] else MISMATCH
        return result

    def report(self, proposal_layout=None):
        items = [self.assess(url, proposal_layout) for url in self.urls()]
        return {'items': items, 'counts': counts(item['status'] for item in items)}
