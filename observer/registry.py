"""Rule proposals, trial state, scoped approvals and effective configuration.

All state lives in the same SQLite file as the page archive so that a page
version, the confirmation made about it and the rule scope that used it can
be traced together.  Approvals are immutable per proposal; changing a scope
means a new proposal and a new version, never an edit in place.
"""

import sqlite3

from .rules import FIELDS, get_rule
from .trial import CONFIRMED, RuleSet, Trial


SCHEMA = '''
CREATE TABLE IF NOT EXISTS trial_items(
    url TEXT PRIMARY KEY, added_at INTEGER NOT NULL DEFAULT 0, removed_at INTEGER);
CREATE TABLE IF NOT EXISTS confirmations(
    id INTEGER PRIMARY KEY, url TEXT NOT NULL, page_id INTEGER NOT NULL,
    digest TEXT NOT NULL, observed INTEGER NOT NULL, fields TEXT NOT NULL,
    operator TEXT NOT NULL, rejected INTEGER NOT NULL DEFAULT 0, withdrawn_at INTEGER);
CREATE TABLE IF NOT EXISTS rule_proposals(
    id INTEGER PRIMARY KEY, rule TEXT NOT NULL, layout TEXT NOT NULL,
    fields TEXT NOT NULL, created_at INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS proposal_scope(
    proposal_id INTEGER NOT NULL, url TEXT NOT NULL,
    PRIMARY KEY(proposal_id, url));
CREATE TABLE IF NOT EXISTS proposal_prefix(
    proposal_id INTEGER NOT NULL, prefix TEXT NOT NULL,
    PRIMARY KEY(proposal_id, prefix));
CREATE TABLE IF NOT EXISTS versions(
    id INTEGER PRIMARY KEY, proposal_id INTEGER NOT NULL UNIQUE,
    rule TEXT NOT NULL, layout TEXT NOT NULL, fields TEXT NOT NULL,
    operator TEXT NOT NULL, created_at INTEGER NOT NULL DEFAULT 0,
    withdrawn_at INTEGER);
CREATE TABLE IF NOT EXISTS version_pages(
    version_id INTEGER NOT NULL, url TEXT NOT NULL, page_id INTEGER NOT NULL,
    digest TEXT NOT NULL, status TEXT NOT NULL,
    PRIMARY KEY(version_id, url));
CREATE TABLE IF NOT EXISTS version_prefix(
    version_id INTEGER NOT NULL, prefix TEXT NOT NULL,
    PRIMARY KEY(version_id, prefix));
CREATE TABLE IF NOT EXISTS isolated_layouts(layout TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS isolated_prefixes(
    layout TEXT NOT NULL, prefix TEXT NOT NULL, PRIMARY KEY(layout, prefix));
CREATE TABLE IF NOT EXISTS isolated_urls(
    layout TEXT NOT NULL, url TEXT NOT NULL, PRIMARY KEY(layout, url));
CREATE TABLE IF NOT EXISTS parse_attempts(
    id INTEGER PRIMARY KEY, page_id INTEGER NOT NULL, url TEXT NOT NULL,
    observed INTEGER NOT NULL, layout TEXT, version_id INTEGER, rule TEXT,
    outcome TEXT NOT NULL, missing TEXT, detail TEXT,
    created_at INTEGER NOT NULL DEFAULT 0);
'''

MISSING = 'missing_required_fields'
UNSUPPORTED = 'unsupported_layout'
REJECTED = 'rejected_page'
ANOMALY = 'layout_anomaly'


class Registry:
    def __init__(self, path, archive, candidates=('legacy', 'folded')):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.archive = archive
        self.candidates = tuple(candidates)

    def trial(self, candidates=None):
        return Trial(self.db, self.archive, RuleSet(candidates or self.candidates))

    # -- proposals -------------------------------------------------------

    def propose(self, rule_name, layout, fields=FIELDS):
        get_rule(rule_name)  # rules must exist; no dangling proposals
        missing = [field for field in FIELDS if field not in fields]
        if missing:
            raise ValueError('proposal must cover all fields, missing ' + ','.join(missing))
        if not layout:
            raise ValueError('proposal needs a layout')
        with self.db:
            cursor = self.db.execute(
                'INSERT INTO rule_proposals(rule, layout, fields) VALUES(?,?,?)',
                (rule_name, layout, ','.join(FIELDS)))
        return cursor.lastrowid

    def _proposal(self, proposal_id):
        row = self.db.execute(
            'SELECT * FROM rule_proposals WHERE id=?', (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return dict(row)

    def propose_scope(self, proposal_id, urls):
        self._proposal(proposal_id)
        with self.db:
            for url in dict.fromkeys(urls):
                self.db.execute(
                    'INSERT OR IGNORE INTO proposal_scope(proposal_id, url) VALUES(?,?)',
                    (proposal_id, url))

    def propose_prefixes(self, proposal_id, prefixes):
        """Region (or section) prefixes the approved rule will cover.

        Trial pages prove the rule; prefixes let newly captured pages in the
        same region use the approved scope without a second approval.
        """
        self._proposal(proposal_id)
        with self.db:
            for prefix in dict.fromkeys(prefix for prefix in prefixes if prefix):
                self.db.execute(
                    'INSERT OR IGNORE INTO proposal_prefix(proposal_id, prefix) VALUES(?,?)',
                    (proposal_id, prefix))

    def scope(self, proposal_id):
        return [row[0] for row in self.db.execute(
            'SELECT url FROM proposal_scope WHERE proposal_id=? ORDER BY url', (proposal_id,))]

    def prefixes(self, proposal_id):
        return [row[0] for row in self.db.execute(
            'SELECT prefix FROM proposal_prefix WHERE proposal_id=? ORDER BY prefix',
            (proposal_id,))]

    # -- approval gate ---------------------------------------------------

    def gate(self, proposal_id, trial=None):
        """Everything that must hold before an approval may take effect.

        Unsupported layouts, pages changed since confirmation and pages
        never confirmed are all blocking.  Trial pages outside the proposed
        scope are blocking too: an uncovered layout must not be presented
        as verified.
        """
        trial = trial or self.trial()
        proposal = self._proposal(proposal_id)
        urls = self.scope(proposal_id)
        problems = []
        evidence = []
        if not urls:
            problems.append({'kind': 'empty_scope', 'detail': 'proposal has no trial pages'})
        for url in urls:
            result = trial.assess(url, proposal['layout'])
            evidence.append(result)
            if result['status'] != CONFIRMED:
                problems.append({'kind': 'unconfirmed_scope', 'url': url,
                                 'status': result['status'], 'reason': result['reason']})
            elif result.get('layout') != proposal['layout']:
                problems.append({'kind': 'layout_mismatch', 'url': url,
                                 'expected': proposal['layout'],
                                 'found': result.get('layout')})
        prefixes = self.prefixes(proposal_id)
        for url in urls:
            if prefixes and not any(url.startswith(prefix) for prefix in prefixes):
                problems.append({'kind': 'scope_outside_prefix', 'url': url})
        for prefix in prefixes:
            if not any(url.startswith(prefix) for url in urls):
                problems.append({'kind': 'prefix_without_evidence', 'prefix': prefix})
        # Pages of the same layout that are in the trial but outside the
        # proposed scope are unverified and blocking.  Pages of other
        # layouts belong to their own proposals.
        approved = [row[0] for row in self.db.execute(
            'SELECT url FROM proposal_scope WHERE proposal_id=?', (proposal_id,))]
        for row in self.db.execute(
                'SELECT url FROM trial_items WHERE removed_at IS NULL'):
            url = row[0]
            if url in approved:
                continue
            result = trial.assess(url)
            if result.get('layout') != proposal['layout']:
                continue
            if self.active(url, proposal['layout']) is not None:
                continue  # already verified by another live approval
            problems.append({'kind': 'trial_pages_not_covered', 'url': url})
        return {'proposal_id': proposal_id, 'approved': not problems,
                'problems': problems, 'evidence': evidence,
                'scope': {'rule': proposal['rule'], 'layout': proposal['layout'],
                          'fields': list(FIELDS)}}

    def approve(self, proposal_id, operator='operator', trial=None):
        """Publish the scope together with the exact pages it was proven on.

        Repeated approval returns the already published version, so a retry
        cannot create a second, subtly different scope.
        """
        gate = self.gate(proposal_id, trial)
        if not gate['approved']:
            raise ValueError('proposal is not ready: ' + ','.join(
                sorted({problem['kind'] for problem in gate['problems']})))
        with self.db:
            row = self.db.execute(
                'SELECT id FROM versions WHERE proposal_id=?', (proposal_id,)).fetchone()
            if row is not None:
                return row[0]
            proposal = self._proposal(proposal_id)
            cursor = self.db.execute(
                'INSERT INTO versions(proposal_id, rule, layout, fields, operator) '
                'VALUES(?,?,?,?,?)',
                (proposal_id, proposal['rule'], proposal['layout'],
                 ','.join(FIELDS), operator))
            version_id = cursor.lastrowid
            for item in gate['evidence']:
                self.db.execute(
                    'INSERT INTO version_pages(version_id, url, page_id, digest, status) '
                    'VALUES(?,?,?,?,?)',
                    (version_id, item['url'], item['page_id'], item['digest'], CONFIRMED))
            for prefix in self.prefixes(proposal_id):
                self.db.execute(
                    'INSERT INTO version_prefix(version_id, prefix) VALUES(?,?)',
                    (version_id, prefix))
        return version_id

    def withdraw(self, version_id, operator='operator'):
        with self.db:
            self.db.execute(
                'UPDATE versions SET withdrawn_at=1 WHERE id=? AND withdrawn_at IS NULL',
                (version_id,))

    def version(self, version_id):
        row = self.db.execute('SELECT * FROM versions WHERE id=?', (version_id,)).fetchone()
        if row is None:
            raise KeyError(version_id)
        return dict(row)

    def versions(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM versions ORDER BY id')]

    # -- effective configuration -----------------------------------------

    def coverage(self, url, layout):
        """Every live approved version that covers ``url``/``layout``.

        Each entry is ``(specificity, version_id, version, prefix)``.  An
        exact trial URL outranks every prefix; among prefixes the longest
        (most specific region) wins.

        Note: ``url`` is a bound parameter and is never interpolated.
        """
        covered = []
        for row in self.db.execute(
                'SELECT v.* FROM versions v JOIN version_pages p ON p.version_id=v.id '
                'WHERE v.withdrawn_at IS NULL AND p.url=? AND v.layout=?',
                (url, layout)):
            covered.append((float('inf'), row['id'], dict(row), None))
        for row in self.db.execute(
                'SELECT v.*, x.prefix AS prefix FROM versions v '
                'JOIN version_prefix x ON x.version_id=v.id '
                'WHERE v.withdrawn_at IS NULL AND v.layout=?', (layout,)):
            if url.startswith(row['prefix']):
                covered.append((len(row['prefix']), row['id'], dict(row), row['prefix']))
        return sorted(covered, key=lambda item: (item[0], item[1]), reverse=True)

    def withdrawn_coverage(self, url, layout):
        """Whether a withdrawn approval covered ``url``/``layout``.

        Used only to report *why* production stopped, never to parse.
        """
        for row in self.db.execute(
                'SELECT v.id FROM versions v JOIN version_pages p ON p.version_id=v.id '
                'WHERE v.withdrawn_at IS NOT NULL AND p.url=? AND v.layout=?',
                (url, layout)):
            return True
        for row in self.db.execute(
                'SELECT x.prefix FROM versions v JOIN version_prefix x ON x.version_id=v.id '
                'WHERE v.withdrawn_at IS NOT NULL AND v.layout=?', (layout,)):
            if url.startswith(row[0]):
                return True
        return False

    def active(self, url, layout):
        """Most specific live approved version for ``url``/``layout``.

        Returns ``None`` when nothing approved covers the page or when the
        matching scope is isolated.  Callers must quarantine rather than
        fall back to an older or unverified rule.
        """
        if layout is None or self.isolated(layout):
            return None
        # Isolation wins over every coverage entry: once a scope is closed,
        # no approval (including a more specific exact-URL one) may publish
        # from it until the operator releases that scope.
        if self.isolation_for(url, layout) is not None:
            return None
        for _, _, version, prefix in self.coverage(url, layout):
            version['_prefix'] = prefix
            return version
        return None

    def scope_isolated(self, layout, prefix, url):
        if prefix is None:
            return self.db.execute(
                'SELECT 1 FROM isolated_urls WHERE layout=? AND url=?',
                (layout, url)).fetchone() is not None
        return self.db.execute(
            'SELECT 1 FROM isolated_prefixes WHERE layout=? AND prefix=?',
            (layout, prefix)).fetchone() is not None

    def isolation_for(self, url, layout):
        """Describe the isolation blocking ``url``/``layout``, if any."""
        if self.isolated(layout):
            return {'layout': layout, 'scope': 'layout'}
        row = self.db.execute(
            'SELECT 1 FROM isolated_urls WHERE layout=? AND url=?',
            (layout, url)).fetchone()
        if row is not None:
            return {'layout': layout, 'scope': 'url', 'url': url}
        best = None
        for row in self.db.execute(
                'SELECT prefix FROM isolated_prefixes WHERE layout=?', (layout,)):
            if url.startswith(row[0]) and (best is None or len(row[0]) > len(best)):
                best = row[0]
        if best is not None:
            return {'layout': layout, 'scope': 'prefix', 'prefix': best}
        return None

    def isolate_scope(self, layout, prefix, url):
        """Isolate exactly the scope that produced the anomaly."""
        with self.db:
            if prefix is None:
                self.db.execute(
                    'INSERT OR IGNORE INTO isolated_urls(layout, url) VALUES(?,?)',
                    (layout, url))
            else:
                self.db.execute(
                    'INSERT OR IGNORE INTO isolated_prefixes(layout, prefix) VALUES(?,?)',
                    (layout, prefix))

    def release_scope(self, layout, prefix, url=None):
        with self.db:
            if prefix is None:
                self.db.execute('DELETE FROM isolated_urls WHERE layout=? AND url=?',
                                (layout, url))
            else:
                self.db.execute('DELETE FROM isolated_prefixes WHERE layout=? AND prefix=?',
                                (layout, prefix))

    def isolated_prefixes(self):
        return [{'layout': row[0], 'prefix': row[1]} for row in self.db.execute(
            'SELECT layout, prefix FROM isolated_prefixes ORDER BY layout, prefix')]

    def isolated_urls(self):
        return [{'layout': row[0], 'url': row[1]} for row in self.db.execute(
            'SELECT layout, url FROM isolated_urls ORDER BY layout, url')]

    def isolated(self, layout):
        return self.db.execute(
            'SELECT 1 FROM isolated_layouts WHERE layout=?', (layout,)).fetchone() is not None

    def isolate(self, layout):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO isolated_layouts(layout) VALUES(?)', (layout,))

    def isolated_layouts(self):
        return sorted(row[0] for row in self.db.execute('SELECT layout FROM isolated_layouts'))

    def release(self, layout):
        with self.db:
            self.db.execute('DELETE FROM isolated_layouts WHERE layout=?', (layout,))
