import pytest

from collector import Crawl, Crawler, Request, ResponseStatusError, Settings
from collector.testing import FakeHttp, make_crawler, make_response


@pytest.mark.parametrize('status', [200, 204, 404])
def test_explicit_expected_statuses(status):
    response = make_response('', request=Request(url='https://example.test/'), status=status)
    assert response.expect_status(status) is None
    assert response.expect_status(200, 204, 404) is None


def test_mismatch_exposes_status_expectations_and_request_url():
    response = make_response('', request=Request(url='https://example.test/'), status=503)
    with pytest.raises(ResponseStatusError) as caught:
        response.expect_status(200, 204)
    error = caught.value
    assert error.status == 503
    assert error.expected_statuses == (200, 204)
    assert error.url == 'https://example.test/'
    assert str(error) == 'Expected HTTP 200, 204, got 503 for https://example.test/'


def test_at_least_one_expected_status_is_required():
    response = make_response('', request=Request(url='https://example.test/'))
    with pytest.raises(TypeError):
        response.expect_status()


@pytest.mark.parametrize('check', [False, True])
async def test_check_is_opt_in_and_uses_normal_engine_errors(check):
    errors = []

    class Page(Crawler):
        name = 'page'
        start_urls = ['https://example.test/']
        settings = Settings(max_errors=0)

        async def parse(self, response):
            if check:
                response.expect_status(200)
            yield response.status

        async def on_error(self, request, error):
            errors.append(error)

    from collector.testing import FakeResponse

    http = FakeHttp({('GET', Page.start_urls[0]): FakeResponse(status_code=404)})
    run = Crawl(make_crawler(Page, http=http))
    if check:
        with pytest.raises(ResponseStatusError) as caught:
            await run.run()
        assert errors == [caught.value]
        assert run.errors[0][1] is caught.value
        assert run.stats.errors == 1
        assert run.stats.reason == 'max_errors'
    else:
        await run.run()
        assert run.stats.items == 1
        assert run.stats.errors == 0
        assert errors == []
