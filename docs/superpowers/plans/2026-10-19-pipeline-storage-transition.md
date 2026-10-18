# Settings Pipelines and Storage Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task after user review. Steps use checkbox (`- [ ]`) syntax for tracking. Native execution in the existing branch is proposed; do not spawn agents without explicit authorization.

**Goal:** Подключать пользовательские pipeline через Settings и удалить встроенное хранение результатов из collector.

**Architecture:** Каждый Crawl создаёт собственную упорядоченную цепочку компонентов и управляет их жизненным циклом. Pipeline преобразуют элементы и реализуют прикладные эффекты; движок изолирует ошибки между обходами. Запись, чтение и экспорт принадлежат приложению.

**Tech Stack:** Python >=3.11, asyncio, dataclasses, существующие pytest/pytest-asyncio и Ruff; без новых зависимостей.

**Spec:** `docs/superpowers/specs/2026-10-19-pipeline-storage-transition-design.md` (согласована пользователем 2026-10-19).

## Global Constraints

- Ветка `codex/pipeline-storage-transition`; не переключать main, не сливать и не публиковать автоматически.
- Не менять trading_platform, его uv.lock и существующее изменение pyproject.toml.
- Не запускать MongoDB, миграции, ETL, live-тесты или сбор данных.
- Не добавлять зависимости pymongo, itemadapter, драйверы баз и обязательные схемы items.
- Python остаётся >=3.11. Сетевые тесты остаются opt-in.
- mark_incomplete, collector.testing, HTTP status helpers и inprotect — отдельные задачи.
- Удалить Dataset API явно, без silent fallback; пользовательские SQLite-файлы не трогать.
- Не добавлять готовые storage/pipeline backend-классы в src/collector.
- Примеры компонентов приложения допустимы в examples, без эффектов при импорте.

## Review Focus

- Отмена во время второго open: первый компонент закрывается, частичная очистка второго остаётся его обязанностью (задача 2).
- Повторная отмена во время close: очистка завершается до возврата CancelledError, фоновых задач не остаётся (задача 3).
- Одновременные pipeline failure и зависший worker: завершение не ждёт опустошения уже не обрабатываемой очереди (задача 3).
- Преобразование item в None: это значение доходит до stream/collect, а не теряется как признак отсутствия элемента (задача 3).
- Отдельные экземпляры одного класса краулера в crawl_many не разделяют состояние компонентов (задача 3).

## Подготовка и команды

Проверить ветку и существующий diff до записи файлов. Документ дизайна уже staged;
не смешивать чужие изменения и не откатывать их. В окружении Git требует
`-c safe.directory=G:/hub/barabacker/collector-framework` для каждой команды;
глобальную конфигурацию и ACL не менять.

Локальные инструменты обнаружены в `.venv/Scripts`. Использовать их без установки
зависимостей, предварительно проверив запуск. Для импорта текущих исходников:

```powershell
$env:PYTHONPATH = 'G:\hub\barabacker\collector-framework\src'
& .\.venv\Scripts\python.exe -m pytest -q -m 'not network'
& .\.venv\Scripts\ruff.exe check .
& .\.venv\Scripts\ruff.exe format --check .
```

Базовый pytest запускать перед реализацией. Если среда не работает или базовые
тесты падают, зафиксировать причину до изменения кода; не маскировать её заменой
версий и не запускать сетевые проверки. Включить stdout/stderr и exit code в отчёт.

Коммиты делать по задачам после проверок. Пока Git identity не настроен, не
придумывать author: сохранять проверенные изменения, сообщить о невозможности
коммита. Это не блокирует код и offline-проверки. Не менять номер релиза/lock
ради этой ветки; несовместимость документируется в CHANGELOG [Unreleased].

### Task 1: Защитить жизненный цикл стартовых запросов

**Files:** Modify `src/collector/engine/crawl.py`; Test `tests/test_crawl.py`.

**Interfaces:** Consumes `Crawler.opened()`, `start_requests()`, `closed(stats)`.
Produces прежний `Crawl.run() -> Stats` с гарантированным завершением после successful opened.

- [x] Добавить `test_start_requests_failure_closes_opened_crawler`: start_requests
  выбрасывает конкретный RuntimeError; проверить identity исключения, один closed,
  `stats.finished_at is not None`, отсутствие HTTP calls.
- [x] Добавить `test_cancel_during_start_requests_closes_crawler`: синхронизировать
  событиями asyncio.Event; отменить после входа в start_requests; проверить
  CancelledError, reason == 'cancelled', один closed, конечное время.
- [x] Запустить `python -m pytest tests/test_crawl.py -q -k 'start_requests or opened'`
  через локальный interpreter: новые тесты должны падать из-за отсутствующего закрытия.
- [x] Расширить try/finally в run; заранее создать пустой список workers и флаг
  успешного opened. Вызывать closed только при successful opened. Сохранить
  действующие max_requests/max_errors и Dataset flush на промежуточном шаге.
- [x] Запустить `python -m pytest tests/test_crawl.py tests/test_runner.py -q`:
  ожидаются PASS, включая существующий тест отказа opened без вызова closed.
- [x] Зафиксировать `fix: close crawlers when start request generation fails` при доступной identity.

### Task 2: Конфигурация и менеджер компонентов

**Files:** Modify `src/collector/settings.py`; Create `src/collector/engine/pipeline.py`,
`tests/test_pipeline.py`; Modify `tests/test_settings.py`.

**Interfaces:** Settings добавляет `pipelines: Sequence[type] = ()` и
`pipeline_options: Mapping[str, Any] = field(default_factory=dict)`.
Внутренний `PipelineManager(crawler: Crawler)` предоставляет
`async open() -> None`, `async process(item: Any) -> Any`,
`async close() -> list[Exception]`. Менеджер не становится корневым экспортом.
Создание экземпляров происходит внутри open, а не конструктора manager.

- [x] Тесты Settings: пустые defaults, разные dict у разных Settings(), replace
  сохраняет базовые настройки; типизированные HTTP-поля работают как прежде.
- [x] Тесты manager: factory получает тот же crawler; fallback cls(); два manager
  получают разные экземпляры; порядок open/process/close == прямой/прямой/обратный;
  цепочка sync и async методов превращает 1 -> 2 -> 4; None не теряется.
- [x] Тесты: optional open/close; отсутствие callable process_item даёт TypeError
  до открытия ресурсов; экземпляр вместо класса в pipelines даёт понятный TypeError.
- [x] Тесты: ошибка/отмена второго open не закрывает второй автоматически, но
  после вызова manager.close закрывает первый; ошибка одного close не мешает
  оставшимся, исключения возвращаются в порядке закрытия; повторный close no-op.
- [x] Запустить `python -m pytest tests/test_pipeline.py tests/test_settings.py -q`:
  новые тесты должны FAIL до появления полей и manager.
- [x] Реализовать manager: создать/проверить компоненты, открывать по порядку;
  записывать компонент в список открытых только после успеха optional open;
  использовать inspect.isawaitable для результата каждого lifecycle/item метода.
  close извлекает компоненты из списка и накапливает обычные Exception, не проглатывая
  CancelledError. Дожидание cleanup при отмене организует Crawl в следующей задаче.
- [x] Повторить целевые тесты: PASS. Ruff для новых/изменённых файлов: PASS.
- [x] Зафиксировать `feat: configure per-crawl item pipelines` при доступной identity.

### Task 3: Интеграция, фатальные ошибки и отмена

**Files:** Modify `src/collector/engine/crawl.py`; Test `tests/test_crawl.py`,
`tests/test_runner.py`, `tests/test_many.py`; extend `tests/test_pipeline.py` only
для локальных reusable test doubles. Не делать публичный collector.testing.

**Interfaces:** Crawl владеет внутренним manager. Все entry points используют
Settings.pipeline configuration без нового аргумента pipelines=. Исходное
исключение сохраняется: прямой run raises original, open_crawl wraps CrawlError,
Outcome.error is original. Новая reason: 'pipeline_error'.

- [x] Тест happy path: open pipeline -> crawler.opened -> start_requests ->
  crawler.process_item -> pipeline.process_item -> crawler.closed -> pipeline.close.
  Стартовые запросы и closed видят живой sink. Перехватить build_http_client через
  существующий SessionHttp, не открывать настоящий HTTP.
- [x] Тесты stream и sync collect получают transformed item и None. Прежний
  crawler.process_item return игнорируется. Stats.items равен числу emitted items.
- [x] Тест pipeline process exception при max_errors=None: одна failed Request в
  crawl.errors, stats.errors == 1, reason == 'pipeline_error', исключение идентично.
  В crawl_many второй краулер завершается успешно, instances pipelines различны.
- [x] Тест concurrency=2: один process_item падает, другой worker ждёт Event;
  queued requests не отправляются после fatal flag, run завершается в ограниченном
  timeout, зависший worker отменён до close. Не использовать sleep для порядка.
- [x] Тест setup/factory/close failures: Outcome.crawl сохранён, finished_at задан,
  errors не содержит фиктивной Request. Тест обычной ошибки parse и legacy
  process_item: прежний max_errors по-прежнему действует.
- [x] Тест primary + close failures: первичная ошибка остаётся исходной, вторичные
  присутствуют в notes/log; close всех компонентов всё равно вызван. Если primary
  нет, первая ошибка close становится ошибкой запуска с reason pipeline_error.
- [x] Тест раннего break, consumer exception, отмены во время open/start/process
  и повторной отмены во время async close: close начинает ожидать Event, отменить
  второй раз, разрешить close; после await задачи cleanup завершён, CancelledError
  не превращён в обычную ошибку, HTTP закрывается после pipeline.
- [x] Запустить целевые новые тесты и убедиться в FAIL; затем интегрировать manager
  вокруг всего lifecycle Crawl.run. На время перехода Dataset.push_data получает
  transformed item после pipeline и до stream. Сохранить dataset.flush до закрытия
  ресурсов, не теряя исходное исключение.
- [x] Для fatal worker failure использовать отдельный сигнал/состояние первого
  pipeline exception; run ожидает queue completion или fatal signal, затем отменяет
  и дожидается workers. Исключение worker не должно оставлять queue.join навечно.
  Сигнал устанавливать до await и до диагностических hooks. Pipeline errors не
  должны попадать в обычную ветвь max_errors. Внутренние обёртки не выходят наружу.
- [x] Cleanup выполнять в отслеживаемой задаче, защищённой от отмены вызывающего;
  повторные cancellation не должны прерывать её и оставлять orphan tasks. После
  ожидания повторно поднять отмену. stream и aclose также должны дождаться этой
  задачи; удалить/дождаться временных queue.get/wait задач в каждом exit path.
- [x] Запустить `python -m pytest tests/test_pipeline.py tests/test_crawl.py tests/test_runner.py tests/test_many.py -q`:
  PASS, без предупреждений pending task. Ruff: PASS.
- [x] Зафиксировать `feat: run pipelines with isolated failures and deterministic cleanup`.

### Task 4: Удаление storage и документация миграции

**Files:** Delete `src/collector/storage/base.py`, `memory.py`, `sqlite.py`, `__init__.py`,
`tests/test_storage.py`. Modify `src/collector/__init__.py`, `engine/crawl.py`,
`engine/runner.py`, `engine/many.py`, `crawler/crawler.py`; tests `conftest.py`,
`test_crawl.py`, `test_runner.py`, `test_many.py`, `test_package.py`, `test_examples.py`;
`README.md`, `CHANGELOG.md`, `examples/README.md`, `examples/storage.py`, `examples/pipeline.py`.
Create `docs/migration/pipelines.md`.

**Interfaces:** Удаляются Dataset/MemoryDataset/SqliteDataset, Crawl.dataset,
аргумент dataset у open_crawl/crawl/run_crawler/collect/crawl_many. Остаются ctx.sink,
Crawl.errors/stats, Outcome, streaming и Settings pipeline API задачи 2.

- [x] Перед удалением сопоставить storage integration tests с pipeline-тестами:
  сохранение каждого item, ошибки записи/закрытия, совместная работа со stream,
  разделение по crawler.name. Добавить отсутствующие сценарии с fake компонентами.
- [x] В test_package закрепить новый набор __all__ и отсутствие storage backend
  экспортов. В test_runner/test_many проверить устаревший dataset= через реальные
  вызовы (async generator обязательно начать итерировать): ожидается TypeError,
  вызов factory HTTP отсутствует. До удаления старого API эти тесты FAIL.
- [x] Удалить перечисленный storage-код и dataset plumbing, удалить items_of из
  conftest, если больше нет потребителей. Не удалять остальные файлы по glob.
  Перенести необходимые поведенческие проверки прежде удаления backend-тестов.
- [x] examples/storage.py заменить примером пользовательского JSONL pipeline с
  Settings/from_crawler, append вместо удаления данных. I/O выполнять через свой
  to_thread/async boundary и дождаться завершения начатой записи перед close при
  отмене; пример не должен оставлять запись в потоке после закрытия файла.
  Компонент остаётся в examples, а не в collector. Обновить examples/pipeline.py
  и описания; main защищён, импорт не открывает сеть/файл.
- [x] Проверить пример офлайн через FakeHttp/SessionHttp и tmp_path: записаны две
  строки включая Unicode, повторный запуск дописывает, после close файл доступен
  для переименования. Функции чтения/экспорта не добавлять в collector.
- [x] README: конфигурация, lifecycle, параллельные items, async I/O, fatal policy,
  ownership sink, None, данные до/после pipeline, ранний break. В migration note
  перечислить удалённые API, пример до/после, перенос собственного reader/export,
  отсутствие автоматической миграции старых SQLite-файлов и отсутствие транзакций.
- [x] CHANGELOG [Unreleased]: явно Breaking change. Исторические docs/superpowers
  не переписывать; из migration note сослаться на заменяющую спецификацию.
- [x] Запустить полный offline pytest и Ruff по командам подготовки: PASS.
  Поиск `rg -n 'Dataset|dataset=' src tests examples README.md` не должен находить
  действующих импортов/примеров старого API; негативные тесты и указание миграции
  допустимы. Проверить `git diff --check`, полный diff и отсутствие изменений
  данных/consumer. При новых исправлениях повторить затронутые проверки.
- [x] Зафиксировать `refactor!: move persistence out of collector` при доступной identity.

## Завершение и передача

- [x] Провести собственную проверку всего diff против критериев spec, особенно
  приоритет ошибок, отсутствие tasks и совместимость существующей обработки без pipeline.
- [x] Сообщить точные результаты проверок, branch, breaking changes и ограничения.
- [x] Не выпускать пакет, не обновлять trading_platform и не сливать ветку без запроса.

## Самопроверка плана

Все критерии spec покрыты: конфигурация/экземпляры задачей 2, lifecycle задачами 1/3,
transformation/fatal isolation/cancellation задачей 3, удаление/миграция задачей 4.
Review Focus распределён по тестам. План согласован и выполнен; изменения фиксируются единым атомарным коммитом (см. execution ledger).
