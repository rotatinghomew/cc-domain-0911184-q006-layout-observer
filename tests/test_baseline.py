import tempfile
import unittest
from pathlib import Path

from observer.archive import PageArchive
from observer.catalog import Catalog
from observer.extract import Extractor
from observer.ingest import collect
from observer.review import Review
from observer.site import PAGES, serve


class BaselineTests(unittest.TestCase):
    def test_review_binds_fields_to_the_actual_page(self):
        archive = PageArchive(':memory:')
        page_id = archive.capture('http://local/a', 1, 200, 'text/html', PAGES['/shell'].encode())
        page = archive.get(page_id)
        article = Extractor().parse(page)
        self.assertFalse(Review().assess(page, article)['accepted'])
        with self.assertRaises(ValueError):
            Review().assess(page, {**article, 'page_id': page_id + 1})

    def test_legacy_page_pipeline_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory, serve() as (url, pages):
            archive = PageArchive(Path(directory) / 'archive.db')
            filename = Path(directory) / 'catalog.db'
            catalog = Catalog(filename)
            result = collect(url + '/east/legacy', 5, archive, Extractor(), catalog)
            self.assertEqual(result['license'], '区域发行')
            self.assertEqual(Catalog(filename).rows()[0]['page_id'], result['page_id'])
            self.assertEqual(archive.get(result['page_id'])['body'], pages['/east/legacy'].encode())

    def test_archive_keeps_changed_page_versions(self):
        archive = PageArchive(':memory:')
        first = archive.capture('http://local/article', 1, 200, 'text/html', b'old')
        second = archive.capture('http://local/article', 2, 200, 'text/html', b'new')
        self.assertNotEqual(archive.get(first)['digest'], archive.get(second)['digest'])
        self.assertEqual(archive.get(first)['body'], b'old')

    def test_catalog_rejects_older_observation_by_retaining_new(self):
        catalog = Catalog(':memory:')
        for observed, title in [(3, '新'), (1, '旧')]:
            catalog.publish({'url': 'http://local/a', 'observed': observed, 'title': title})
        self.assertEqual(catalog.rows()[0]['title'], '新')

    def test_nonarticle_and_http_failure(self):
        archive = PageArchive(':memory:')
        page_id = archive.capture('http://local/login', 1, 200, 'text/html', PAGES['/login'].encode())
        with self.assertRaises(ValueError):
            Extractor().parse(archive.get(page_id))
        page_id = archive.capture('http://local/error', 2, 503, 'text/html', b'<h1>busy</h1>')
        with self.assertRaises(ValueError):
            Extractor().parse(archive.get(page_id))

    def test_new_layout_is_real_but_not_legacy_article(self):
        archive = PageArchive(':memory:')
        page_id = archive.capture('http://local/folded', 1, 200, 'text/html', PAGES['/east/folded'].encode())
        with self.assertRaises(ValueError):
            Extractor().parse(archive.get(page_id))

    def test_unknown_evidence_is_not_fabricated(self):
        with self.assertRaises(KeyError):
            PageArchive(':memory:').get(90)


if __name__ == '__main__':
    unittest.main()
