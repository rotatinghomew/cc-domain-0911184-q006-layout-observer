"""End-to-end demo of the trial -> approval -> ingest lifecycle.

Runs entirely against local loopback pages and a temporary directory; no
partner site, account or production data is used.  Output is illustrative
and does not represent production coverage.
"""

import json
import tempfile
from pathlib import Path

from observer.archive import PageArchive
from observer.catalog import Catalog
from observer.ingest import Collector
from observer.registry import Registry
from observer.report import effective_report, trial_report
from observer.site import BROKEN_FOLDED, FOLDED, serve


def show(label, value):
    print('\n== ' + label + ' ==')
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    with tempfile.TemporaryDirectory() as directory, serve() as (url, pages):
        base = Path(directory)
        archive = PageArchive(base / 'pages.sqlite3')
        catalog = Catalog(base / 'catalog.sqlite3')
        registry = Registry(base / 'registry.sqlite3', archive)
        trial = registry.trial()
        collector = Collector(archive, catalog, registry)

        east = url + '/east'
        trial_url = east + '/folded'

        # 1. Trial: capture a page and record a human confirmation for that
        #    exact page version.
        page_id = collector.fetch(trial_url, 1)
        trial.add(trial_url)
        trial.confirm(page_id, {'title': '灯火新版', 'author': '合作社乙',
                                'license': '网络发行', 'published': '2026-11-11'},
                      'operator:lin')
        show('trial before partner update', trial_report(trial)['counts'])

        # 2. Partner changes the page during the trial (new body, new digest).
        pages['/east/folded'] = FOLDED.format(
            title='灯火新版', author='合作社乙', license='全国发行', published='2026-11-13')
        collector.fetch(trial_url, 2, capture_key='fetch-2')
        report = trial_report(trial)
        show('trial after page update', {
            'counts': report['counts'],
            'unconfirmed': report['unconfirmed'],
            'changed_fields': report['changed_fields'],
        })

        # 3. Approval gate is blocked until the operator looks at the new
        #    version.  The old confirmation is never mixed with new content.
        proposal = registry.propose('folded', 'folded')
        registry.propose_scope(proposal, [trial_url])
        registry.propose_prefixes(proposal, [east])
        show('approval gate (blocked)', registry.gate(proposal)['problems'])

        current = trial.page(trial_url)
        trial.confirm(current['id'], {'title': '灯火新版', 'author': '合作社乙',
                                      'license': '全国发行', 'published': '2026-11-13'},
                      'operator:lin')
        version_id = registry.approve(proposal, 'operator:lin')
        approved = registry.version(version_id)
        show('approved version', {key: approved[key]
                                  for key in ('id', 'rule', 'layout', 'fields', 'operator')})

        # 4. A newly captured page in the approved region is parsed by the
        #    approved rule, and keeps the evidence it was parsed from.
        fresh = collector.collect(east + '/fresh', 1)
        show('new page ingested', {key: fresh[key] for key in
                                   ('page_id', 'digest', 'layout', 'version_id',
                                    'rule', 'status', 'published')})
        show('published entry', catalog.rows()[0])

        # 5. Anomaly: a folded page loses its license block.  Only this
        #    region's folded scope is isolated; other layouts keep working
        #    and raw pages keep being archived.
        broken = archive.capture(east + '/broken', 2, 200, 'text/html; charset=utf-8',
                                 BROKEN_FOLDED.encode())
        show('anomaly', collector.ingest(broken))

        show('effective state', effective_report(registry, catalog, archive))


if __name__ == '__main__':
    main()
