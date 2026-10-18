# Public crawler testing helpers

Дата: 2026-10-19. Статус: проект спецификации после согласования направления.
Ветка: `codex/public-testing`, основана на `de6409b` (переход на pipeline).

## Задача и границы

Дать авторам краулеров небольшой `collector.testing`: создание краулера без
сборки CrawlerContext, создание настоящего Response из текста, разделение результатов
callback, подставной HTTP для целого Crawl и его pipeline. Никакой зависимости
от pytest, MongoDB, предметных моделей, структуры проекта или запуска внешних систем.
Функции работают в Python >=3.11 с существующими зависимостями collector.

Production runtime не импортирует testing. Корневой __all__ не расширяется:
инструменты импортируются явно из collector.testing. Без нового run_test_crawler,
отдельного движка или изменения аргументов open_crawl.

## Публичный API

```python
def make_crawler(
    crawler_cls: type[C],
    *,
    params: Mapping[str, Any] | None = None,
    sink: Any = None,
    http: Any = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> C: ...


def make_response(
    text: str,
    *,
    request: Request,
    crawler: Crawler | None = None,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> Response: ...


async def collect_callback(
    results: AsyncIterable[Any],
) -> tuple[list[Request], list[Any]]: ...
```

C — TypeVar, ограниченный Crawler. make_crawler вызывает настоящий конструктор,
сохраняет тип подкласса и штатную проверку params. Если http не передан, создаёт
пустой строгий FakeHttp: незапланированный HTTP-вызов вызывает LookupError с
методом и URL, никогда не открывает сеть. Явно переданный объект http не заменяется
по его truthiness. sink и log передаются как есть, владение ими не меняется.
Функция не вызывает opened/closed, pipeline или start_requests.

make_response возвращает настоящий Response, сохраняет Request и его metadata,
задаёт raw URL из request.url и UTF-8 content из text. Заголовки обеспечивают
case-insensitive lookup, как реальные HTTP-заголовки. Для follow/form_request
нужно передать crawler; без него доступны selector/json/headers и прочие чистые
операции Response. Публичный runtime Response при этом не меняется.

collect_callback потребляет async iterable один раз, относит isinstance(Request)
в первую группу, все остальные значения (включая None) во вторую. Порядок внутри
групп сохраняется. Исключения и отмена распространяются без упаковки. Запросы не
выполняются, элементы не проходят через pipeline и не влияют на Stats.

## FakeResponse и FakeHttp

FakeResponse — публичный простой raw-ответ для фикстур: text, status_code, url,
headers, content. По умолчанию статус 200, пустые text/url/headers; content при
отсутствии явного значения получается из UTF-8 text. Словари не разделяются между
экземплярами. Это не полный эмулятор curl_cffi.Response.

```python
http = FakeHttp(
    {
        ('GET', 'https://example.test/'): '<a href="/next">Next</a>',
        ('GET', 'https://example.test/next'): FakeResponse(text='done', status_code=200),
        ('POST', 'https://example.test/fail'): OSError('offline failure'),
    }
)
crawler = make_crawler(MyCrawler, http=http)
stats = await Crawl(crawler).run()
```

Ключ — (method, URL). Метод нормализуется в uppercase; URL сопоставляется буквально,
без собственной канонизации. kwargs записываются, но не участвуют в маршрутизации.
Значение — str, FakeResponse или Exception. Строка даёт ответ 200; пустой URL
FakeResponse заполняется URL запроса без изменения исходной фикстуры. Исключение
поднимается как есть. Отсутствующая фикстура даёт LookupError с методом и URL.

Повторный запрос возвращает ту же заданную фикстуру по содержимому; очереди ответов,
маршрутизация по body, имитация задержек, retry, hooks, TLS и throttle в первый этап
не входят. Для таких сценариев приложение может передать собственный http.

calls содержит (method, URL), sent — соответствующие kwargs, включая неуспешные
попытки. Копируется верхний уровень kwargs; произвольные вложенные объекты не клонируются.
Асинхронный контекст и close() выставляют closed; запрос после закрытия — RuntimeError.
Повторный close безопасен. Прямой Crawl.run не закрывает внешний http автоматически;
владелец использует async with FakeHttp(...), если проверяет его жизненный цикл.
Для open_crawl/crawl_many тест подменяет build_http_client обычным средством своего
test runner; публичного monkeypatch-контекста в collector не добавляется.

## Использование текущих тестовых объектов

Переиспользовать FakeResponse и вынести полезную общую механику FakeHttp из
tests/conftest.py. Существующие тесты местами полагаются на пустой ответ для любого
URL: сохранить это как явно внутренний совместимый адаптер, а не ослаблять
строгий публичный FakeHttp. ctx_factory с pytest.fixture остаётся в tests.
Хотя бы один тест callback и один тест полного обхода с pipeline должны использовать
публичный API: проверить, что он действительно уменьшает ручную сборку объектов.

## Проверки

- Создание краулера сохраняет тип, настройки, params и переданные sink/http/log;
  ошибочные params отклоняются штатным механизмом; lifecycle не запускается.
- Пустой http исключает случайную сеть; незнакомая страница не маскируется ответом 200.
- Response поддерживает selector/json, metadata, follow, form_request и заголовки.
- Callback смешивает Request, dict, None; группы и порядок корректны; ошибка/отмена
  остаются исходными. Никаких скрытых запусков pipeline.
- FakeHttp различает GET/POST одного URL, записывает kwargs, отдаёт статусы/тела,
  воспроизводит исключения и сохраняет исходные фикстуры.
- close/контекст не допускают запросов после закрытия; повторный close безопасен.
- Полный обход с FakeHttp выполняет пагинацию и pipeline, сохраняет Stats;
  ошибка отсутствующей фикстуры проходит обычную политику ошибок движка.
- Все существующие offline-тесты и Ruff проходят без новых зависимостей.
  Mongo/live/network тесты не запускать; trading_platform не менять.

## Переход и совместимость

Добавляется src/collector/testing.py и тесты публичных помощников. Существующие
production exports/entry points не меняются. README получает примеры тестирования
callback и всего обхода; CHANGELOG отмечает новый публичный модуль.
Спецификация не включает полноту обхода, status helpers, inprotect и перенос
фикстур потребителя. Пошаговый план и реализация следуют после проверки документа.
