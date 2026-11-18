"""Operational reports: differences, unconfirmed scope, effective state.

Counts of "parsed" pages are deliberately not the headline.  What is
reported is which page version and rule were compared, what changed, which
scope could not be confirmed, and what is actually live right now.
"""

from .trial import counts


def trial_report(trial, proposal_id=None, proposal_layout=None):
    report = trial.report(proposal_layout)
    items = []
    for item in report['items']:
        item = dict(item)
        item['usable'] = item['status'] == 'confirmed'
        items.append(item)
    return {'proposal_id': proposal_id, 'items': items,
            'counts': counts(item['status'] for item in items),
            'unconfirmed': [{'url': item['url'], 'status': item['status'],
                             'reason': item['reason'], 'page_id': item['page_id'],
                             'digest': item['digest']}
                            for item in items if not item['usable']],
            'changed_fields': [{'url': item['url'], 'field': change['field'],
                                'confirmed': change['confirmed'],
                                'candidate': change['candidate']}
                               for item in items for change in item['changes']]}


def effective_report(registry, catalog, archive):
    """What is live, for which scope, and which attempts did not produce."""
    layouts = []
    for version in registry.versions():
        if version['withdrawn_at']:
            continue
        urls = [row[0] for row in registry.db.execute(
            'SELECT url FROM version_pages WHERE version_id=? ORDER BY url', (version['id'],))]
        prefixes = [row[0] for row in registry.db.execute(
            'SELECT prefix FROM version_prefix WHERE version_id=? ORDER BY prefix',
            (version['id'],))]
        isolated_prefixes = {entry['prefix'] for entry in registry.isolated_prefixes()
                             if entry['layout'] == version['layout']}
        isolated_urls = {entry['url'] for entry in registry.isolated_urls()
                         if entry['layout'] == version['layout']}
        layouts.append({'version_id': version['id'], 'rule': version['rule'],
                        'layout': version['layout'], 'fields': version['fields'].split(','),
                        'proven_on': urls, 'prefixes': prefixes,
                        'isolated': (registry.isolated(version['layout'])
                                     or any(prefix in isolated_prefixes for prefix in prefixes)
                                     or bool(set(urls) & isolated_urls))})
    published = catalog.rows()
    attempts = [{'outcome': row[0], 'layout': row[1], 'count': row[2]}
                for row in registry.db.execute(
                    'SELECT outcome, layout, COUNT(*) FROM parse_attempts '
                    'GROUP BY outcome, layout ORDER BY outcome, layout')]
    missing = [{'url': row[0], 'page_id': row[1], 'layout': row[2], 'missing': row[3]}
               for row in registry.db.execute(
                   'SELECT url, page_id, layout, missing FROM parse_attempts '
                   "WHERE outcome='quarantined' AND missing<>'' ORDER BY id")]
    return {'versions': layouts,
            'isolated_layouts': registry.isolated_layouts(),
            'isolated_scopes': registry.isolated_prefixes() + registry.isolated_urls(),
            'published': {'count': len(published),
                          'by_rule': counts(article.get('rule', 'unknown')
                                            for article in published)},
            'attempts': attempts,
            'missing_fields': missing}
