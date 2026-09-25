"""A JSON API: per-request ``params``, a second callback, and ``metadata``.

Nothing about the framework assumes HTML. ``response.json()`` is the whole of
the difference, and a paged API is a pagination chain like any other — here the
page number rides on ``params`` rather than in the path.

It also shows the two things a ``Request`` carries that a URL cannot: a
``callback`` other than ``parse()``, for a page with a different shape, and
``metadata``, which arrives untouched on the response that answers it.

    uv run python examples/json_api.py
"""

from __future__ import annotations

import sys
from typing import Any

from collector import Crawler, Response, Settings, collect

API = 'https://quotes.toscrape.com/api/quotes'


class QuotesApi(Crawler):
    name = 'quotes-api'
    settings = Settings(delay=0.3, max_requests=5)

    async def start_requests(self) -> Any:
        # A start that a URL alone cannot express, which is what this override
        # is for: the first page is page 1, and the API says so in a param.
        yield self.request(API, params={'page': 1}, metadata={'page': 1})

    async def parse(self, response: Response) -> Any:
        body = response.json()
        page = response.metadata['page']

        for quote in body['quotes']:
            yield {
                'page': page,
                'author': quote['author']['name'],
                'text': quote['text'],
                'tags': quote['tags'],
            }
            # The author's own page is HTML, not JSON, so it needs a callback
            # that knows that. Yielding a Request enqueues it.
            yield self.request(
                response.urljoin(f'/author/{quote["author"]["slug"]}/'),
                callback=self.parse_author,
                metadata={'name': quote['author']['name']},
            )

        if body['has_next']:
            yield self.request(
                API,
                params={'page': page + 1},
                metadata={'page': page + 1},
            )

    async def parse_author(self, response: Response) -> Any:
        yield {
            'author': response.metadata['name'],
            'born': response.selector().css('span.author-born-date::text').get(),
        }


def main() -> None:
    # Scraped text is not ASCII and a Windows console is not UTF-8 by default,
    # which is a UnicodeEncodeError the first time an author is called André.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # Two callbacks, so two shapes of item come back in one list — the
    # framework has no schema and does not mind.
    items = collect(QuotesApi)
    quotes = [item for item in items if 'text' in item]
    authors = [item for item in items if 'born' in item]

    print(f'{len(quotes)} quotes, {len(authors)} author pages')
    if quotes:
        print(f'  page {quotes[0]["page"]}: {quotes[0]["author"]} — {quotes[0]["text"][:50]}…')
    if authors:
        print(f'  {authors[0]["author"]} born {authors[0]["born"]}')


if __name__ == '__main__':
    main()
