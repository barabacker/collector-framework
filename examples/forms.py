"""A form: log in by posting the page's own form back, CSRF token and all.

The login page on the sandbox carries a hidden ``csrf_token`` that has to go
back with the credentials. ``form_request()`` collects every field the form
would send — the token included, without this crawler naming it — and lays
``formdata`` over them. No submit button is pressed unless ``click=`` names one.

The sandbox accepts any username and password.

    uv run python examples/forms.py
"""

from __future__ import annotations

import sys
from typing import Any

from collector import Crawler, Response, Settings, collect


class Login(Crawler):
    name = 'login'
    start_urls = ['https://quotes.toscrape.com/login']
    settings = Settings(delay=0.3, max_requests=3)

    async def parse(self, response: Response) -> Any:
        yield response.form_request(
            formdata={'username': 'demo', 'password': 'demo'},
            callback=self.after_login,
        )

    async def after_login(self, response: Response) -> Any:
        page = response.selector()
        yield {
            # Only a logged-in page links to /logout.
            'logged_in': bool(page.css('a[href="/logout"]')),
            'first_quote': page.css('div.quote span.text::text').get(),
        }


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    for item in collect(Login):
        print(f'logged in: {item["logged_in"]}')
        print(f'  first quote: {item["first_quote"]}')


if __name__ == '__main__':
    main()
