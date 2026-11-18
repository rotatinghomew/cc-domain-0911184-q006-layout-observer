import tempfile
import unittest
from pathlib import Path

from observer.archive import PageArchive
from observer.catalog import Catalog
from observer.ingest import Collector, ACCEPTED, ISOLATED, QUARANTINED, UNSUPPORTED
from observer.registry import Registry
from observer.report import effective_report, trial_report
from observer.rules import Extractor, RuleError
from observer.site import BROKEN_FOLDED, PAGES, serve
from observer.trial import CHANGED, CONFIRMED, MISMATCH, UNSUPPORTED as T_UNSUPPORTED


class Fixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.archive = PageArchive(base / 'pages.db')
        self.catalog = Catalog(base / 'catalog.db')
        self.registry = Registry(base / 'registry.db', self.archive)
        self.collector = Collector(self.archive, self.catalog, self.registry)
        self.trial = self.registry.trial()

    def capture(self, url, observed, html, status=200, content_type='text/html; charset=utf-8',
                capture_key=None):
        return self.archive.capture(url, observed, status, content_type, html.encode(),
                                    capture_key=capture_key)

    def approve_folded(self, urls):
        proposal = self.registry.propose('folded', 'folded')
        self.registry.propose_scope(proposal, urls)
        return proposal, self.registry.approve(proposal)


class RulesAndPageNatureTests(Fixture):
    def test_folded_page_is_extracted_only_by_its_rule(self):
        page_id = self.capture('http://local/f', 1, PAGES['/east/folded'])
        page = self.archive.get(page_id)
        with self.assertRaises(RuleError):
            Extractor('legacy').parse(page)
        fields = Extractor('folded').parse(page)
        self.assertEqual(fields['license'], '网络发行')
        self.assertEqual(fields['published'], '2026-11-11')
        self.assertEqual(fields['author'], '合作社乙')

    def test_login_and_shell_are_not_articles_despite_200(self):
        for path in ('/login', '/shell'):
            page_id = self.capture('http://local' + path, 1, PAGES[path])
            with self.assertRaises(RuleError) as caught:
                Extractor('folded').parse(self.archive.get(page_id))
            self.assertIn(caught.exception.code, ('login_page', 'empty_shell'))

    def test_unmarked_layout_is_unsupported_not_empty(self):
        page_id = self.capture('http://local/u', 1, PAGES['/unmarked'])
        out = self.collector.ingest(page_id)
        self.assertEqual(out['status'], UNSUPPORTED)
        self.assertEqual(self.catalog.rows(), [])


class TrialTests(Fixture):
    def test_confirmation_is_pinned_to_page_version(self):
        url = 'http://local/f'
        old = self.capture(url, 1, PAGES['/east/folded'])
        self.trial.add(url)
        self.trial.confirm(old, {'title': '灯火新版', 'author': '合作社乙',
                                 'license': '网络发行', 'published': '2026-11-11'}, 'ops')
        self.assertEqual(self.trial.assess(url)['status'], CONFIRMED)

        # Partner updates the page during the trial.
        new = self.capture(url, 2, PAGES['/west/folded'])
        result = self.trial.assess(url)
        self.assertEqual(result['status'], CHANGED)
        self.assertEqual(result['page_id'], new)
        self.assertEqual(result['confirmation_page_id'], old)
        self.assertNotEqual(result['digest'], result['confirmation_digest'])

    def test_removed_pages_leave_the_trial_scope(self):
        url = 'http://local/f'
        self.capture(url, 1, PAGES['/east/folded'])
        self.trial.add(url)
        self.trial.remove(url)
        self.assertEqual(self.trial.urls(), [])

    def test_candidate_error_is_reported_not_confirmed(self):
        url = 'http://local/login'
        page_id = self.capture(url, 1, PAGES['/login'])
        self.trial.add(url)
        self.trial.confirm(page_id, {'title': '登录'}, 'ops')
        result = self.trial.assess(url)
        self.assertEqual(result['status'], T_UNSUPPORTED)

    def test_report_names_versions_rule_and_field_changes(self):
        url = 'http://local/f'
        page_id = self.capture(url, 1, PAGES['/east/folded'])
        self.trial.add(url)
        self.trial.confirm(page_id, {'title': '灯火新版', 'author': '合作社乙',
                                     'license': '旧授权', 'published': '2026-11-11'}, 'ops')
        report = trial_report(self.trial)
        change = report['changed_fields'][0]
        self.assertEqual(change['field'], 'license')
        self.assertEqual(change['confirmed'], '旧授权')
        self.assertEqual(change['candidate'], '网络发行')
        item = report['items'][0]
        self.assertEqual(item['page_id'], page_id)
        self.assertEqual(item['rule'], 'folded')
        self.assertFalse(item['usable'])
        self.assertEqual(report['counts'][MISMATCH], 1)


class ApprovalGateTests(Fixture):
    def test_unconfirmed_and_changed_scope_blocks_approval(self):
        a = self.capture('http://local/a', 1, PAGES['/east/folded'])
        b = self.capture('http://local/b', 1, PAGES['/west/folded'])
        self.trial.add('http://local/a')
        self.trial.add('http://local/b')
        self.trial.confirm(a, {'title': '灯火新版', 'author': '合作社乙',
                               'license': '网络发行', 'published': '2026-11-11'}, 'ops')
        self.trial.confirm(b, {'title': '归途新版', 'author': '合作社丁',
                               'license': '西部发行', 'published': '2026-11-12'}, 'ops')
        proposal = self.registry.propose('folded', 'folded')
        self.registry.propose_scope(proposal, ['http://local/a', 'http://local/b'])
        # Partner updates b after confirmation.
        self.capture('http://local/b', 2, PAGES['/east/folded'])
        gate = self.registry.gate(proposal)
        self.assertFalse(gate['approved'])
        self.assertEqual([p['kind'] for p in gate['problems']], ['unconfirmed_scope'])
        with self.assertRaises(ValueError):
            self.registry.approve(proposal)

    def test_uncovered_trial_page_blocks_verification(self):
        a = self.capture('http://local/a', 1, PAGES['/east/folded'])
        # Same layout, in the trial, but left out of the proposed scope.
        self.capture('http://local/other', 1, PAGES['/west/folded'])
        self.trial.add('http://local/a')
        self.trial.add('http://local/other')
        self.trial.confirm(a, {'title': '灯火新版', 'author': '合作社乙',
                               'license': '网络发行', 'published': '2026-11-11'}, 'ops')
        proposal = self.registry.propose('folded', 'folded')
        self.registry.propose_scope(proposal, ['http://local/a'])
        gate = self.registry.gate(proposal)
        self.assertFalse(gate['approved'])
        self.assertIn('trial_pages_not_covered',
                      [p['kind'] for p in gate['problems']])

    def test_approval_pins_evidence_and_is_idempotent(self):
        url = 'http://local/a'
        page_id = self.capture(url, 1, PAGES['/east/folded'])
        self.trial.add(url)
        self.trial.confirm(page_id, {'title': '灯火新版', 'author': '合作社乙',
                                     'license': '网络发行', 'published': '2026-11-11'}, 'ops')
        proposal = self.registry.propose('folded', 'folded')
        self.registry.propose_scope(proposal, [url])
        first = self.registry.approve(proposal)
        second = self.registry.approve(proposal)
        self.assertEqual(first, second)
        self.assertEqual(len(self.registry.versions()), 1)
        evidence = self.registry.db.execute(
            'SELECT page_id, digest FROM version_pages WHERE version_id=?', (first,)).fetchone()
        self.assertEqual(evidence[0], page_id)
        self.assertEqual(evidence[1], self.archive.get(page_id)['digest'])


class IngestTests(Fixture):
    def _commit_folded(self):
        url = 'http://local/f'
        page_id = self.capture(url, 1, PAGES['/east/folded'])
        self.trial.add(url)
        self.trial.confirm(page_id, {'title': '灯火新版', 'author': '合作社乙',
                                     'license': '网络发行', 'published': '2026-11-11'}, 'ops')
        proposal, version_id = self.approve_folded([url])
        return url, version_id

    def test_approved_layout_publishes_with_evidence(self):
        url, version_id = self._commit_folded()
        page_id = self.capture(url, 2, PAGES['/east/folded'])
        out = self.collector.ingest(page_id)
        self.assertEqual(out['status'], ACCEPTED)
        self.assertEqual(out['version_id'], version_id)
        article = self.catalog.rows()[0]
        self.assertEqual(article['license'], '网络发行')
        self.assertEqual(article['page_id'], page_id)
        self.assertEqual(article['digest'], self.archive.get(page_id)['digest'])
        self.assertEqual(article['rule_version'], 1)

    def test_unapproved_url_is_never_parsed_by_default_rule(self):
        self._commit_folded()
        page_id = self.capture('http://local/other', 1, PAGES['/east/folded'])
        out = self.collector.ingest(page_id)
        self.assertEqual(out['status'], UNSUPPORTED)
        self.assertEqual(self.catalog.rows(), [])

    def test_inflight_and_retried_capture_keep_one_version(self):
        url, _ = self._commit_folded()
        first = self.capture(url, 3, PAGES['/east/folded'], capture_key='fetch-1')
        retry = self.capture(url, 3, PAGES['/east/folded'], capture_key='fetch-1')
        self.assertEqual(first, retry)
        self.assertEqual(self.collector.ingest(first)['status'], ACCEPTED)
        self.assertEqual(self.collector.ingest(retry)['status'], ACCEPTED)
        self.assertEqual(len(self.catalog.rows()), 1)

    def test_reparse_does_not_flip_rule_or_roll_back(self):
        url, version_id = self._commit_folded()
        page_id = self.capture(url, 3, PAGES['/east/folded'])
        self.assertEqual(self.collector.ingest(page_id)['version_id'], version_id)
        # A newer approved rule appears for the same url/layout.
        new_proposal = self.registry.propose('folded', 'folded')
        self.registry.propose_scope(new_proposal, [url])
        newer = self.registry.approve(new_proposal)
        self.assertNotEqual(newer, version_id)
        # Reparse of the already decided item stays on its pinned version.
        self.assertEqual(self.collector.reparse(page_id)['version_id'], version_id)

    def test_anomaly_isolates_one_layout_and_others_continue(self):
        url, _ = self._commit_folded()
        self.capture(url, 2, PAGES['/east/folded'])
        # A different approved legacy scope must keep producing.
        legacy_url = 'http://local/l'
        legacy_page = self.capture(legacy_url, 1, PAGES['/east/legacy'])
        self.trial.add(legacy_url)
        self.trial.confirm(legacy_page, {'title': '灯火', 'author': '合作社甲',
                                         'license': '区域发行', 'published': '2026-11-10'}, 'ops')
        proposal = self.registry.propose('legacy', 'legacy')
        self.registry.propose_scope(proposal, [legacy_url])
        self.registry.approve(proposal)

        broken = self.capture(url, 3, BROKEN_FOLDED)
        out = self.collector.ingest(broken)
        self.assertEqual(out['status'], QUARANTINED)
        self.assertIn('license', out['missing'])
        # This approval has no region prefix, so isolation covers the URL.
        self.assertEqual(self.registry.isolated_urls(),
                         [{'layout': 'folded', 'url': url}])
        self.assertEqual(self.archive.get(broken)['body'], BROKEN_FOLDED.encode())

        # Folded is now isolated even for pages that would parse fine.
        healthy = self.capture(url, 4, PAGES['/east/folded'])
        self.assertEqual(self.collector.ingest(healthy)['status'], ISOLATED)
        # Legacy keeps working.
        legacy_next = self.capture(legacy_url, 2, PAGES['/east/legacy'])
        self.assertEqual(self.collector.ingest(legacy_next)['status'], ACCEPTED)

        # Raw pages are still collected while isolated.
        bodies = [page['body'].decode() for page in self.archive.observations(url)]
        self.assertEqual(len(bodies), 4)
        self.assertIn(BROKEN_FOLDED, bodies)

    def test_effective_report_shows_live_scope_and_gaps(self):
        url, version_id = self._commit_folded()
        page_id = self.capture(url, 2, PAGES['/east/folded'])
        self.collector.ingest(page_id)
        report = effective_report(self.registry, self.catalog, self.archive)
        self.assertEqual(report['published']['count'], 1)
        self.assertEqual(report['published']['by_rule'], {'folded': 1})
        self.assertEqual(report['versions'][0]['proven_on'], [url])
        self.assertEqual(report['versions'][0]['version_id'], version_id)


if __name__ == '__main__':
    unittest.main()


class RegionScopeTests(Fixture):
    def _approve_region(self, prefix, trial_url=None):
        trial_url = trial_url or prefix + '/proven'
        page_id = self.capture(trial_url, 1, PAGES['/east/folded'])
        self.trial.add(trial_url)
        self.trial.confirm(page_id, {'title': '灯火新版', 'author': '合作社乙',
                                     'license': '网络发行', 'published': '2026-11-11'}, 'ops')
        proposal = self.registry.propose('folded', 'folded')
        self.registry.propose_scope(proposal, [trial_url])
        self.registry.propose_prefixes(proposal, [prefix])
        return self.registry.approve(proposal)

    def test_new_pages_in_approved_region_use_approved_scope(self):
        prefix = 'http://local/east'
        version_id = self._approve_region(prefix)
        new_page = self.capture(prefix + '/fresh', 1, PAGES['/west/folded'])
        out = self.collector.ingest(new_page)
        self.assertEqual(out['status'], ACCEPTED)
        self.assertEqual(out['version_id'], version_id)
        self.assertEqual(self.catalog.rows()[0]['license'], '西部发行')

    def test_prefix_without_evidence_blocks_approval(self):
        trial_url = 'http://local/east/proven'
        page_id = self.capture(trial_url, 1, PAGES['/east/folded'])
        self.trial.add(trial_url)
        self.trial.confirm(page_id, {'title': '灯火新版', 'author': '合作社乙',
                                     'license': '网络发行', 'published': '2026-11-11'}, 'ops')
        proposal = self.registry.propose('folded', 'folded')
        self.registry.propose_scope(proposal, [trial_url])
        self.registry.propose_prefixes(proposal, ['http://local/west'])
        gate = self.registry.gate(proposal)
        self.assertIn('prefix_without_evidence', [p['kind'] for p in gate['problems']])

    def test_isolation_is_confined_to_its_region(self):
        east = 'http://local/east'
        west = 'http://local/west'
        self._approve_region(east, east + '/proven')
        self._approve_region(west, west + '/proven')
        broken = self.capture(east + '/broken', 2, BROKEN_FOLDED)
        self.assertEqual(self.collector.ingest(broken)['status'], QUARANTINED)
        # The same layout in another approved region keeps publishing.
        healthy = self.capture(west + '/fresh', 1, PAGES['/east/folded'])
        self.assertEqual(self.collector.ingest(healthy)['status'], ACCEPTED)
        # The isolated region still archives raw pages.
        isolated_page = self.capture(east + '/fresh', 1, PAGES['/east/folded'])
        out = self.collector.ingest(isolated_page)
        self.assertEqual(out['status'], ISOLATED)
        self.assertEqual(self.archive.get(isolated_page)['body'], PAGES['/east/folded'].encode())


class EdgeCaseTests(Fixture):
    def _approve(self, url, html=PAGES['/east/folded'], prefixes=()):
        page_id = self.capture(url, 1, html)
        self.trial.add(url)
        self.trial.confirm(page_id, {'title': '灯火新版', 'author': '合作社乙',
                                     'license': '网络发行', 'published': '2026-11-11'}, 'ops')
        proposal = self.registry.propose('folded', 'folded')
        self.registry.propose_scope(proposal, [url])
        if prefixes:
            self.registry.propose_prefixes(proposal, prefixes)
        return self.registry.approve(proposal)

    def test_login_page_is_rejected_even_under_approved_scope(self):
        url = 'http://local/east/folded'
        self._approve(url)
        login = self.capture(url, 2, PAGES['/login'])
        out = self.collector.ingest(login)
        self.assertEqual(out['status'], 'rejected_page')
        self.assertEqual(self.catalog.rows(), [])

    def test_withdrawn_rule_stops_producing_for_pinned_items(self):
        url = 'http://local/east/folded'
        version_id = self._approve(url)
        self.registry.withdraw(version_id)
        page_id = self.capture(url, 2, PAGES['/east/folded'])
        out = self.collector.ingest(page_id)
        self.assertEqual(out['status'], 'withdrawn_rule')
        self.assertEqual(self.catalog.rows(), [])

    def test_undecodable_body_is_rejected_not_crashing(self):
        url = 'http://local/east/folded'
        self._approve(url)
        page_id = self.archive.capture(url, 2, 200, 'text/html', b'\xff\xfe\x00bad')
        out = self.collector.ingest(page_id)
        self.assertEqual(out['status'], 'rejected_page')
        self.assertEqual(out['detail'], 'undecodable')
        self.assertEqual(self.archive.get(page_id)['body'], b'\xff\xfe\x00bad')

    def test_prefixed_isolation_does_not_block_sibling_prefix(self):
        east = 'http://local/east'
        west = 'http://local/west'
        self._approve(east + '/proven', prefixes=[east])
        self._approve(west + '/proven', prefixes=[west])
        broken = self.capture(east + '/broken', 2, BROKEN_FOLDED)
        self.assertEqual(self.collector.ingest(broken)['status'], QUARANTINED)
        ok = self.capture(west + '/fresh', 1, PAGES['/east/folded'])
        self.assertEqual(self.collector.ingest(ok)['status'], 'accepted')

    def test_release_scope_resumes_production_from_archived_pages(self):
        east = 'http://local/east'
        self._approve(east + '/proven', prefixes=[east])
        broken = self.capture(east + '/broken', 2, BROKEN_FOLDED)
        self.collector.ingest(broken)
        self.registry.release_scope('folded', east)
        healthy = self.capture(east + '/fresh', 3, PAGES['/east/folded'])
        self.assertEqual(self.collector.ingest(healthy)['status'], 'accepted')

    def test_unmarked_page_never_becomes_verified_in_trial(self):
        url = 'http://local/unmarked'
        page_id = self.capture(url, 1, PAGES['/unmarked'])
        self.trial.add(url)
        self.trial.confirm(page_id, {'title': '无标记'}, 'ops')
        result = self.trial.assess(url)
        self.assertEqual(result['status'], T_UNSUPPORTED)
        self.assertNotEqual(result['status'], CONFIRMED)


class IsolationDominanceTests(Fixture):
    def test_prefix_isolation_also_closes_exact_trial_url(self):
        east = 'http://local/east'
        proven = east + '/proven'
        page_id = self.capture(proven, 1, PAGES['/east/folded'])
        self.trial.add(proven)
        self.trial.confirm(page_id, {'title': '灯火新版', 'author': '合作社乙',
                                     'license': '网络发行', 'published': '2026-11-11'}, 'ops')
        proposal = self.registry.propose('folded', 'folded')
        self.registry.propose_scope(proposal, [proven])
        self.registry.propose_prefixes(proposal, [east])
        self.registry.approve(proposal)

        broken = self.capture(east + '/broken', 2, BROKEN_FOLDED)
        self.assertEqual(self.collector.ingest(broken)['status'], 'quarantined')
        # The exact trial URL must also stop, not slip through its own
        # version_pages row.
        healthy = self.capture(proven, 3, PAGES['/east/folded'])
        self.assertEqual(self.collector.ingest(healthy)['status'], 'isolated')
        self.assertEqual(len(self.catalog.rows()), 0)
