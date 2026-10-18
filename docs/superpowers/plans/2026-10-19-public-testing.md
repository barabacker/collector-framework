# Public crawler testing helpers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Execute in this session without subagents, as previously chosen by the user.

**Goal:** Дать авторам краулеров публичные инструменты для тестирования callback и полного обхода без сети.

**Architecture:** Один независимый модуль `collector.testing` создаёт настоящие Crawler/Response и подставляет HTTP-ответы. Полный обход использует существующий Crawl; lifecycle и pipeline выполняет существующий движок. Совместимость прежних тестов обеспечивает внутренний адаптер в conftest.

**Tech Stack:** Python >=3.11, существующие curl_cffi/parsel; pytest и pytest-asyncio только для тестов, Ruff.

**Spec:** [Согласованная спецификация](../specs/2026-10-19-public-testing-design.md).

## Global Constraints

- Production runtime не импортирует testing. Корневой __all__ не расширяется.
- Без новых зависимостей, runner, аргументов open_crawl, MongoDB и предметных фикстур.
- Не менять trading_platform; не запускать Mongo/live/network тесты.
- Переданные sink/http/log остаются ресурсами вызывающего кода.
- Прямые вызовы callback не запускают lifecycle/pipeline и не меняют Stats.
- Работать в `codex/public-testing`, сохранить чужие изменения; не выполнять merge/push.

## Review Focus

- Явно переданный falsy http должен сохраниться по идентичности (задача 2).
- Явный content=b'' не должен заменяться UTF-8 тела (задача 1).
- Повторные запросы с одной FakeResponse не должны изменять исходные url/headers (задача 1).
- Отмена async iterable должна распространяться без упаковки (задача 2).
- Ошибка отсутствующей страницы должна проходить штатную политику max_errors (задача 3).

## Файлы и проверки

- Create `src/collector/testing.py`: FakeResponse, FakeHttp, три публичных помощника.
- Create `tests/test_testing_http.py`: контракт транспорта и raw-ответов.
- Create `tests/test_testing.py`: создание объектов и разделение callback.
- Create `tests/test_testing_crawl.py`: пагинация, pipeline, ошибки движка.
- Modify `tests/conftest.py`: переиспользование публичных фикстур через совместимый адаптер.
- Modify `README.md`, `CHANGELOG.md`: примеры и описание нового публичного модуля.

Перед началом прочитать спецификацию, `src/collector/crawler/crawler.py`,
`src/collector/crawler/response.py`, `src/collector/engine/crawl.py`,
`tests/conftest.py`, `tests/test_pipeline_crawl.py`, `tests/test_crawler_params.py`.
Проверить status и последние коммиты. Выполнять команды из корня репозитория.

В текущем окружении использовать системный Python с существующими зависимостями:

```powershell
$env:PYTHONPATH = 'G:\hub\barabacker\collector-framework\src;G:\hub\barabacker\collector-framework\.venv\Lib\site-packages'
$env:PYTHONASYNCIODEBUG = '1'
python -W error -m pytest -q -m 'not network' -p no:cacheprovider -o asyncio_default_fixture_loop_scope=function --tb=short
& .\.venv\Scripts\ruff.exe check .
& .\.venv\Scripts\ruff.exe format --check .
git -c safe.directory=G:/hub/barabacker/collector-framework diff --check
```

Для целевых проверок добавлять pytest пути файлов из соответствующей задачи.
Ожидаемый результат: все выбранные тесты проходят, без warnings; Ruff и diff-check успешны.
Не менять ACL или глобальный safe.directory для обхода ограничений окружения.

## Task 1: Публичные HTTP-фикстуры

**Files:** Create `src/collector/testing.py`, `tests/test_testing_http.py`; modify `tests/conftest.py`.

**Interfaces:**
- `FakeResponse(text: str = '', status_code: int = 200, url: str = '', content: bytes | None = None, headers: Mapping[str, str] | None = None)`; content после создания — bytes, headers поддерживает case-insensitive lookup.
- `FakeHttp(pages: Mapping[tuple[str, str], str | FakeResponse | Exception] | None = None)`.
- `async request(self, method: str, url: str, **kwargs: Any) -> FakeResponse`.
- `async close(self) -> None`, `async __aenter__(self) -> FakeHttp`, `async __aexit__(self, *exc_info: Any) -> None`.
- Атрибуты `calls: list[tuple[str, str]]`, `sent: list[dict[str, Any]]`, `closed: bool`.

- [ ] Написать тесты defaults/UTF-8/явного пустого content и независимости headers. Проверить `FakeResponse(text='я').content == 'я'.encode('utf-8')`, `FakeResponse(text='x', content=b'').content == b''`, заголовок `Content-Type` доступен как `content-type`.
- [ ] Написать тесты маршрутов GET/POST одного URL, lowercase метода, буквального URL, возврата str/статуса/Exception. Для исключения проверить `caught.value is failure`; для неизвестного URL — LookupError с методом и URL.
- [ ] Написать тесты повторных запросов: исходная FakeResponse сохраняет пустой url и headers, полученный ответ имеет URL запроса. Проверить calls/sent для успешных и неуспешных попыток, поверхностное копирование kwargs.
- [ ] Написать тесты `closed` после контекста и исключения внутри контекста, повторного close, RuntimeError после close. События запросов и словари разных клиентов независимы.
- [ ] Запустить `tests/test_testing_http.py`: до реализации ожидается отсутствие публичного модуля/API.
- [ ] Реализовать фикстуры в testing.py. Использовать существующий `curl_cffi.requests.headers.Headers`, предварительно проверив его API в установленном пакете. Нормализовать методы при регистрации и запросе, URL не менять. Копировать raw-ответ и headers при выдаче; не клонировать произвольные вложенные kwargs. Отсутствие маршрута никогда не вызывает настоящий транспорт.
- [ ] Заменить внутреннюю FakeResponse на публичную. Сохранить прежние параметры FakeHttp(pages с URL-ключами, default_body) и SessionHttp через внутренний адаптер; запись calls/sent делегировать публичному request. Не менять ctx_factory и не ослаблять публичный fail-fast контракт.
- [ ] Запустить целевые и существующие offline-тесты: устранить только реальные несовместимости адаптера, без ослабления assertions.
- [ ] Проверить Ruff и diff; commit `feat: expose offline HTTP testing fixtures` с явным списком файлов.

## Task 2: Создание краулера и тестирование callback

**Files:** Modify `src/collector/testing.py`; create `tests/test_testing.py`.

**Interfaces:** Использует FakeHttp/FakeResponse задачи 1. Производит:

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

C — TypeVar bound Crawler; typing-импорты совместимы с Python 3.11.

- [ ] Написать тест make_crawler: настоящий подкласс, штатная проверка params, идентичность sink/http/log, сохранённые settings. В частности `crawler.http is falsy_http`; счётчики opened/closed/start_requests/pipeline остаются пустыми. Клиент по умолчанию выдаёт LookupError при запросе.
- [ ] Написать тест make_response: `response.request is request`, `response.metadata is request.metadata`, status, UTF-8 raw.content, raw.url, заголовки, selector и json. На HTML с формой проверить разрешение follow и form_request относительно request.url через переданный crawler.
- [ ] Написать тест collect_callback со смешанными Request, dict и None: `requests == [first, second]`, `items == [item, None]`. Проверить один проход custom async iterable, исходное исключение и CancelledError без упаковки; pipeline не вызван.
- [ ] Запустить `tests/test_testing.py`: ожидается отсутствие помощников.
- [ ] Реализовать помощники через настоящий CrawlerContext/конструктор подкласса/Response. Проверять `http is None`; когда log не задан, сохранять стандартный log контекста. Не перехватывать ошибки callback и не добавлять lifecycle.
- [ ] Запустить `tests/test_testing.py` и `tests/test_testing_http.py`; проверить Ruff/diff.
- [ ] Commit `feat: add crawler callback testing helpers` с явным списком файлов.

## Task 3: Полный обход и документация

**Files:** Create `tests/test_testing_crawl.py`; modify `README.md`, `CHANGELOG.md`.

**Interfaces:** Использует только публичные помощники задач 1–2 и существующие Crawl/Settings; production entry points не меняются.

- [ ] Написать тест краулера с двумя страницами и pipeline: создать через make_crawler, выполнить Crawl.run, проверить порядок open/process/close, обработанные элементы, `stats.items == 2`, завершённость статистики и два HTTP-вызова. До выхода из async with FakeHttp клиент открыт; после выхода закрыт.
- [ ] Написать тест отсутствующей второй страницы с `max_errors=1`: проверить штатную ошибку/причину остановки текущего Crawl, `stats.errors == 1` и закрытие pipeline. Точный тип внешней ошибки сверить с существующим контрактом Crawl.run, не вводить новую упаковку.
- [ ] Запустить `tests/test_testing_crawl.py`: это интеграционная проверка уже реализованных частей; исправлять только публичные помощники, если выявлена их ошибка.
- [ ] Добавить README-примеры прямого callback и полного Crawl с FakeHttp. Явно описать method+URL, отсутствие автоматического запуска pipeline при collect_callback, заимствованный http и ограничения fake (нет hooks/retry/сетевой эмуляции). Отметить модуль в CHANGELOG.
- [ ] Выполнить полную offline-проверку, Ruff и diff-check командами выше. Проверить diff: root exports, runtime, зависимости и потребитель не изменены.
- [ ] Самостоятельно проверить покрытие спецификации, владение ресурсами и ошибки. Исправить выявленное и повторить затронутые проверки.
- [ ] Commit `docs: demonstrate offline crawler and pipeline testing` с интеграционными тестами и документацией. Сообщить результаты, коммиты и состояние ветки; merge/push не выполнять.

## Handoff

План подготовлен после согласования спецификации. Следующий шаг — проверка плана пользователем,
затем выполнение в этой сессии без субагентов. Код реализации пока не изменён.
