[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/README.md) | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/README.md) | **RU**

# Оркестратор Avtomatika

[![Лицензия: MPL 2.0](https://img.shields.io/badge/License-MPL%202.0-brightgreen.svg)](https://opensource.org/licenses/MPL-2.0)
[![Версия PyPI](https://img.shields.io/pypi/v/avtomatika.svg)](https://pypi.org/project/avtomatika/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/release/python-3110/)

Avtomatika — это высокопроизводительный движок для управления сложными асинхронными рабочими процессами на Python, основанный на конечных автоматах. Он предоставляет надежный фреймворк для создания масштабируемых и отказоустойчивых приложений, разделяя логику процесса и логику выполнения.

Этот документ служит исчерпывающим руководством для разработчиков, желающих создавать пайплайны (блупринты) и встраивать Оркестратор в свои приложения.

## Оглавление
- [Основная концепция: Оркестратор, Блупринты и Воркеры](#основная-концепция-оркестратор-блупринты-и-воркеры)
- [Установка](#установка)
- [Быстрый старт: Использование в качестве библиотеки](#быстрый-старт-использование-в-качестве-библиотеки)
- [Ключевые концепции: JobContext и Actions](#ключевые-концепции-jobcontext-и-actions)
- [Cookbook Блупринтов: Ключевые возможности](#cookbook-блупринтов-ключевые-возможности)
  - [Условные переходы (.when())](#условные-переходы-when)
  - [Делегирование задач Воркерам (dispatch_task)](#делегирование-задач-воркерам-dispatch_task)
  - [Параллельное выполнение и Агрегация (Fan-out/Fan-in)](#параллельное-выполнение-и-агрегация-fan-outfan-in)
  - [Внедрение зависимостей (DataStore)](#внедрение-зависимостей-datastore)
  - [Нативный Планировщик (Native Scheduler)](#нативный-планировщик-native-scheduler)
  - [S3 Payload Offloading (Работа с файлами)](#s3-payload-offloading-работа-с-файлами)
  - [Уведомления через вебхуки](#уведомления-через-вебхуки)
- [Конфигурация для Production](#конфигурация-для-production)
  - [Файлы конфигурации](#файлы-конфигурации)
  - [Отказоустойчивость](#отказоустойчивость)
  - [Высокая доступность и Распределенная блокировка](#высокая-доступность-и-распределенная-блокировка)
  - [Бэкенд хранилища](#бэкенд-хранилища)
  - [Безопасность](#безопасность)
  - [Наблюдаемость](#наблюдаемость)
- [Руководство участника](#руководство-участника)
  - [Настройка окружения](#настройка-окружения)
  - [Запуск тестов](#запуск-тестов)

## Основная концепция: Оркестратор, Блупринты и Воркеры

Проект основан на простом, но мощном архитектурном паттерне, который отделяет логику процесса от логики выполнения.

*   **Оркестратор (OrchestratorEngine)** — Режиссёр. Он управляет всем процессом от начала до конца, отслеживает состояние, обрабатывает ошибки и решает, что должно произойти дальше. Он не выполняет бизнес-задачи сам.
*   **Блупринты (Blueprint)** — Сценарий. Каждый блупринт — это детальный план (конечный автомат) для конкретного бизнес-процесса. Он описывает шаги (состояния) и правила перехода между ними.
*   **Воркеры (Worker)** — Команда специалистов. Это независимые, специализированные исполнители. Каждый воркер знает, как выполнять определенный набор задач (например, "обработать видео", "отправить email") и отчитывается перед Оркестратором.

## Экосистема

Avtomatika является частью более широкой экосистемы:

*   **[Avtomatika Protocol](https://github.com/avtomatika-ai/rxon)**: Общий пакет с определениями протокола, моделями данных и утилитами, обеспечивающий совместимость всех компонентов.
*   **[Avtomatika Worker SDK](https://github.com/avtomatika-ai/avtomatika-worker)**: Официальный Python SDK для создания воркеров, подключаемых к этому движку.
*   **[Протокол HLN](https://github.com/avtomatika-ai/hln)**: Архитектурная спецификация и манифест, лежащие в основе системы.
*   **[Полный пример (Full Example)](https://github.com/avtomatika-ai/avtomatika-full-example)**: Готовый эталонный проект, демонстрирующий совместную работу движка и воркеров.

## Установка

*   **Установка только ядра движка:**
    ```bash
    pip install avtomatika
    ```

*   **Установка с поддержкой Redis (рекомендуется для production):**
    ```bash
    pip install "avtomatika[redis]"
    ```

*   **Установка с поддержкой хранилища истории (SQLite, PostgreSQL):**
    ```bash
    pip install "avtomatika[history]"
    ```

*   **Установка с поддержкой телеметрии (Prometheus, OpenTelemetry):**
    ```bash
    pip install "avtomatika[telemetry]"
    ```

*   **Установка с поддержкой S3 (Payload Offloading):**
    ```bash
    pip install "avtomatika[s3]"
    ```

*   **Установка всех зависимостей, включая тестовые:**
    ```bash
    pip install "avtomatika[all,test]"
    ```

## Быстрый старт: Использование в качестве библиотеки

Вы можете легко интегрировать и запустить движок оркестратора внутри вашего собственного приложения.

> **Совет:** Имя состояния в `@bp.handler()` и `@bp.aggregator()` теперь необязательно. Если оно не указано, в качестве имени состояния будет использовано имя функции.

```python
# my_app.py
import asyncio
from avtomatika import OrchestratorEngine, Blueprint
from avtomatika.context import ActionFactory
from avtomatika.storage import MemoryStorage
from avtomatika.config import Config

# 1. Общая конфигурация
storage = MemoryStorage()
config = Config() # Загружает конфигурацию из переменных окружения

# Явно задаем токены для этого примера
# Токен клиента должен быть отправлен в заголовке 'X-Client-Token'.
config.CLIENT_TOKEN = "my-secret-client-token"
# Токен воркера должен быть отправлен в заголовке 'X-Worker-Token'.
config.GLOBAL_WORKER_TOKEN = "my-secret-worker-token"

# 2. Определение Блупринта рабочего процесса
bp = Blueprint(
    name="bp",
    api_version="v1",
    api_endpoint="/jobs/my_flow"
)

# Используйте внедрение зависимостей, чтобы получить только нужные данные.
@bp.handler(is_start=True)
async def start(job_id: str, initial_data: dict, actions: ActionFactory):
    """Начальное состояние для каждого нового задания."""
    print(f"Job {job_id} | Start: {initial_data}")
    actions.go_to("end")

# Вы все еще можете запросить полный объект контекста, если предпочитаете.
@bp.handler(is_end=True)
async def end(context):
    """Конечное состояние. Пайплайн заканчивается здесь."""
    print(f"Job {context.job_id} | Complete.")

# 3. Инициализация движка Оркестратора
engine = OrchestratorEngine(storage, config)
engine.register_blueprint(bp)

# 4. Доступ к компонентам (Опционально)
# Вы можете получить доступ к внутреннему приложению aiohttp и компонентам через AppKey
# из avtomatika.app_keys импортируйте ENGINE_KEY, DISPATCHER_KEY и др.
# app = engine.app
# dispatcher = app[DISPATCHER_KEY]

# 5. Определение главной точки входа для запуска сервера
async def main():
    await engine.start()
    
    try:
        await asyncio.Event().wait()
    finally:
        await engine.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановка сервера.")
```

### Жизненный цикл движка: `run()` vs. `start()`

`OrchestratorEngine` предлагает два способа запуска сервера:

*   **`engine.run()`**: Это простой, **блокирующий** метод. Он полезен для отдельных скриптов, где оркестратор является единственным основным компонентом. Он управляет запуском и остановкой сервера за вас. Не следует использовать его внутри `async def` функции, которая является частью большего приложения, так как это может конфликтовать с циклом событий.

*   **`await engine.start()`** и **`await engine.stop()`**: Это неблокирующие методы для интеграции движка в большее `asyncio` приложение.
    *   `start()` настраивает и запускает веб-сервер в фоне.
    *   `stop()` корректно останавливает сервер и очищает ресурсы.
Пример "Быстрый старт" выше демонстрирует правильный способ использования этих методов.

## Ключевые концепции

### Высокопроизводительная Архитектура

Avtomatika спроектирована для высоконагруженных сред с тысячами конкурентных воркеров.

*   **Стандартизированный Holon Matching (RXON v1.0b7)**: Высокопроизводительная маршрутизация с использованием Redis.
    *   **Унифицированный подбор**: Переход на формализованную логику подбора из `rxon`. Все проверки ресурсов (CPU, RAM, GPU и пользовательские свойства) строго регламентированы стандартом протокола HLN.
    *   **Умное сравнение чисел**: Автоматическое выполнение проверок **GE (Больше или равно)** для чисел.
    *   **Deep Schema Matching**: Приоритет отдается воркерам, чья `input_schema` соответствует параметрам задачи.
    *   **Overflow Strategy**: Автоматический перелив нагрузки на более дорогих воркеров, если дешевые перегружены (`queue_length > SOFT_LIMIT`).
    *   **Hot Cache & Skill Awareness**: Учет загруженных AI-моделей в памяти воркеров.
    *   **Work Stealing**: Свободные воркеры могут атомарно «красть» задачи из очередей перегруженных коллег на скорости O(1).
    *   **Load Balancing**: Оптимистичное управление нагрузкой для предотвращения перегрузки узлов.
    *   **Эффективная сеть**: TCP Keep-Alive и сжатие ответов Zstd/Gzip.
    *   **Асинхронный логгинг**: Неблокирующая обработка логов через `QueueHandler`.
    *   **Offloaded IO**: Вынос тяжелой сериализации в потоки и оптимизация индексов БД для истории.
*   **Саморегулирующаяся репутация**:
    *   **Система штрафов**: Мгновенное снижение репутации за нарушение контракта (-0.2) или критические сбои.
    *   **Цикл восстановления**: Небольшие бонусы к репутации за каждое успешное выполнение (+0.001).
    *   **Reputation Guard**: Автоматическое игнорирование узлов с репутацией ниже `REPUTATION_MIN_THRESHOLD`.
*   **Zero Trust Security (Нулевое доверие)**:
    *   **mTLS (Mutual TLS)**: Взаимная аутентификация Оркестратора и Воркеров по сертификатам.
    *   **STS (Security Token Service)**: Механизм ротации токенов.
*   **Архитектура на базе контрактов**:
    *   **Валидация API**: Строгая проверка `initial_data` против контрактов Блюпринтов.
    *   **Контроль результатов**: Автоматическая проверка ответов воркеров на соответствие их `output_schema`.
    *   **Ghost Signaling**: Блюпринты могут генерировать кастомные события через `actions.send_event()`.
*   **Network Visibility**:
    *   **Skill Catalog**: Агрегированный маркетплейс всех уникальных навыков и контрактов в реальном времени.
    *   **Global Registry**: Контракты хранятся в Redis для консистентности всего кластера.
*   **Bi-directional Heartbeats**: Двусторонний канал связи, где оркестратор посылает срочные команды в ответ на оптимизированные Heartbeat с использованием Jitter (защита от Thundering Herd).
*   **Non-Blocking I/O**:
    *   **Вебхуки**: Отправляются через параллельный пул фоновых воркеров.
    *   **S3 Streaming**: Константное потребление памяти при передаче файлов любого размера.

## Книга Рецептов: Ключевые возможности

### 1. Условные переходы (`.when()`)

Используйте `.when()` для создания ветвлений условной логики. Строка условия оценивается движком до вызова обработчика, поэтому она все еще использует префикс `context.`. Сам обработчик, однако, может использовать внедрение зависимостей.

```python
# Условие `.when()` все еще ссылается на `context`.
@bp.handler().when("context.initial_data.type == 'urgent'")
async def decision_step(actions):
    actions.go_to("urgent_processing")

# Обработчик по умолчанию, если ни одно условие `.when()` не совпало.
@bp.handler
async def decision_step(actions):
    actions.go_to("normal_processing")
```

### 2. Делегирование задач Воркерам (`dispatch_task`)

Это основная функция для делегирования работы. Оркестратор поставит задачу в очередь и будет ждать, пока воркер заберет ее и вернет результат.

```python
@bp.handler
async def transcode_video(initial_data, actions):
    actions.dispatch_task(
        task_type="video_transcoding",
        params={"input_path": initial_data.get("path")},
        # Определяем следующий шаг на основе статуса ответа воркера
        transitions={
            "success": "publish_video",
            "failure": "transcoding_failed",
            "needs_review": "manual_review" # Пример кастомного статуса
        }
    )
```
Если воркер вернет статус, не указанный в `transitions`, задание автоматически перейдет в состояние сбоя.

### 3. Параллельное выполнение и Агрегация (Fan-out/Fan-in)

Запуск нескольких задач одновременно и сбор их результатов.

```python
# 1. Fan-out: Отправка нескольких задач для агрегации в одно состояние
@bp.handler
async def process_files(initial_data, actions):
    tasks_to_dispatch = [
        {"task_type": "file_analysis", "params": {"file": file}}
        for file in initial_data.get("files", [])
    ]
    # Используйте dispatch_parallel, чтобы отправить все задачи сразу.
    # Все успешные задачи неявно приведут к состоянию 'aggregate_into'.
    actions.dispatch_parallel(
        tasks=tasks_to_dispatch,
        aggregate_into="aggregate_results"
    )

# 2. Fan-in: Сбор результатов с помощью декоратора @aggregator
@bp.aggregator
async def aggregate_results(aggregation_results, state_history, actions):
    # Этот обработчик выполнится ТОЛЬКО ПОСЛЕ ТОГО, как ВСЕ задачи, 
    # отправленные через dispatch_parallel, будут завершены.

    # aggregation_results это словарь {task_id: result_dict}
    summary = [res.get("data") for res in aggregation_results.values()]
    state_history["summary"] = summary
    actions.go_to("processing_complete")
```

### 4. Внедрение зависимостей (DataStore)

Предоставление обработчикам доступа к внешним ресурсам (например, кэшу или клиенту БД).

```python
import redis.asyncio as redis

# 1. Инициализация и регистрация вашего DataStore
redis_client = redis.Redis(decode_responses=True)
bp = Blueprint(
    "blueprint_with_datastore",
    data_stores={"cache": redis_client}
)

# 2. Использование его в обработчике через внедрение зависимостей
@bp.handler
async def get_from_cache(data_stores):
    # Доступ к redis_client по имени "cache"
    user_data = await data_stores.cache.get("user:123")
    print(f"User from cache: {user_data}")
```

### 5. Нативный Планировщик (Native Scheduler)

Avtomatika включает в себя встроенный распределенный планировщик. Он позволяет запускать блупринты периодически (интервально, ежедневно, еженедельно, ежемесячно) без внешних инструментов типа cron.

*   **Конфигурация:** Определяется в `schedules.toml`.
*   **Часовые пояса:** Поддерживает глобальную настройку часового пояса (например, `TZ="Europe/Moscow"`).
*   **Поддержка протухания:** Поддерживает параметры `dispatch_timeout` и `result_timeout`.
*   **Распределенная блокировка:** Задачи гарантированно запускаются только один раз за интервал благодаря распределенным блокировкам (Redis/Memory).

```toml
# Пример schedules.toml
[nightly_backup]
blueprint = "backup_flow"
daily_at = "02:00"
dispatch_timeout = 60 # Отмена, если ни один воркер не взял задачу за 1 минуту
```

### 6. Вебхуки (Webhook Notifications)

Оркестратор может отправлять асинхронные уведомления во внешнюю систему при завершении, сбое или карантине задачи. 

### 7. Работа с большими данными (S3 Payload Offloading)

Оркестратор предоставляет встроенную поддержку для работы с большими файлами через S3-совместимые хранилища, используя высокопроизводительную библиотеку `obstore` (Rust).

*   **Безопасность памяти (Streaming)**: Потоковая передача данных для загрузки и скачивания.
*   **Управляемый режим**: Автоматическая очистка S3 объектов и локальных временных файлов.
*   **Внедрение зависимостей**: Используйте аргумент `task_files` в ваших хендлерах.

```python
@bp.handler
async def process_data(task_files, actions):
    # Потоковое скачивание большого файла
    local_path = await task_files.download("large_dataset.csv")
    
    # ... обработка данных ...
    
    # Загрузка результата
    await task_files.write_json("results.json", {"status": "done"})
    
    actions.go_to("finished")
```

## API Группы и Версионирование

Все внешние эндпоинты API строго версионированы и имеют префикс `/api/v1/`.

*   **События:**
    *   `job_finished`: Задание достигло финального успеха.
    *   `job_failed`: Задание завершилось ошибкой.
    *   `job_quarantined`: Задание отправлено в карантин после повторных сбоев.

**Пример запроса:**
```json
POST /api/v1/jobs/my_flow
{
    "initial_data": {
        "video_url": "..."
    },
    "webhook_url": "https://my-app.com/webhooks/avtomatika",
    "dispatch_timeout": 30,
    "result_timeout": 120
}
```

**Пример вебхука:**
```json
{
    "event": "job_finished",
    "job_id": "123e4567-e89b-12d3-a456-426614174000",
    "status": "finished",
    "result": {
        "output_path": "/videos/result.mp4"
    },
    "error": null
}
```

## Конфигурация для продакшена

Поведение оркестратора можно настроить через переменные окружения. Также систему отличает **строгая валидация** конфигурационных файлов (`clients.toml`, `workers.toml`) при запуске.

### Отказоустойчивость

*   **TRANSIENT_ERROR**: Временная ошибка. Автоматические ретраи.
*   **PERMANENT_ERROR**: Постоянная ошибка. Немедленный карантин.
*   **INVALID_INPUT_ERROR**: Ошибка во входных данных. Сбой всего задания.

### Гарантии стабильности

*   **Exponential Backoff:** Основные циклы (`JobExecutor`, `Watcher`) автоматически замедляются при сбоях инфраструктуры.
*   **Защита от захвата задач:** Только назначенный воркер может отправить результат.
*   **Защита от циклов:** `MAX_TRANSITIONS_PER_JOB` (по умолчанию 100) завершает зацикленные блупринты.

### Конкурентность и Производительность

*   **`EXECUTOR_MAX_CONCURRENT_JOBS`**: Лимит одновременных обработчиков (по умолчанию: `1000`).
*   **`WATCHER_LIMIT`**: Количество проверяемых таймаутов за цикл (по умолчанию: `500`).
*   **`DISPATCHER_MAX_CANDIDATES`**: Лимит проверки соответствия воркеров (по умолчанию: `50`).

### Наблюдаемость

*   **Структурированное JSON логирование**: Неблокирующая очередь `QueueHandler`.
*   **Метрики**: Доступны по адресу `/_public/metrics`, используют префикс `orchestrator_` и включают `orchestrator_loop_lag_seconds`.

### Слой хранилища

*   **Redis (StorageBackend)**: Хранение состояний (`msgpack`) и очередей (Streams).
*   **PostgreSQL/SQLite (HistoryStorage)**: Архив истории с оптимизированными индексами.

### Режим Чистого Холона (Pure Holon Mode)
Отключите публичный API через `ENABLE_CLIENT_API="false"`, чтобы принимать задачи только по протоколу RXON от родительских узлов.

## Руководство участника

### Настройка окружения

```bash
pip install -e ../rxon
pip install -e ".[all,test]"
```

### Запуск тестов

```bash
pytest tests/
```

### Интерактивная документация API

Доступна по адресу `/_public/docs`. Динамическая генерация на основе блупринтов.

## Подробная документация

-   [**Архитектурный гайд**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/architecture.md)
-   [**Справочник API**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/api_reference.md)
-   [**Гайд по конфигурации**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/configuration.md)
-   [**Гайд по развертыванию**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/deployment.md)
-   [**Cookbook**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/README.md)
звертыванию**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/deployment.md)
-   [**Cookbook**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/README.md)
.md)
