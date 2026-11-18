"""Single-page review.

``Review`` checks that a parsed article refers to the page it came from and
that the required fields are non-empty.  This is a per-page check only: it
is not a human confirmation and says nothing about other layouts.
"""

REQUIRED = ('title', 'author', 'license', 'published')


class Review:
    def assess(self, page, article):
        missing = [field for field in REQUIRED if not article.get(field)]
        if article['page_id'] != page['id'] or article['url'] != page['url']:
            raise ValueError('article does not refer to this page')
        return {'page_id': page['id'], 'digest': page['digest'],
                'rule': article['rule'], 'missing': missing, 'accepted': not missing}
