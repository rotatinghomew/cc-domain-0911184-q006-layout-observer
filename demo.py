import json
import tempfile
from pathlib import Path
from observer.archive import PageArchive
from observer.catalog import Catalog
from observer.extract import Extractor
from observer.ingest import collect
from observer.site import serve


def main():
    with tempfile.TemporaryDirectory() as directory, serve() as (url, pages):
        archive = PageArchive(Path(directory) / 'pages.sqlite3')
        catalog = Catalog(Path(directory) / 'catalog.sqlite3')
        collect(url + '/east/legacy', 1, archive, Extractor(), catalog)
        collect(url + '/west/legacy', 2, archive, Extractor(), catalog)
        print(json.dumps(catalog.rows(), ensure_ascii=False))


if __name__ == '__main__':
    main()
