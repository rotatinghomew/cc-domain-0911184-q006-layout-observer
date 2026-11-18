from urllib.request import urlopen
from .review import Review


def collect(url, observed, archive, extractor, catalog):
    with urlopen(url, timeout=5) as response:
        page_id = archive.capture(url, observed, response.status, response.headers.get('Content-Type', ''), response.read())
    page = archive.get(page_id)
    article = extractor.parse(page)
    assessment = Review().assess(page, article)
    if not assessment['accepted']:
        raise ValueError('article fields are incomplete: ' + ','.join(assessment['missing']))
    catalog.publish(article)
    return article
