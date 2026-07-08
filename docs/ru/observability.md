[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/observability.md) | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/observability.md) | **RU**

# Система наблюдения Avtomatika (Observability)

В проекте реализована современная система наблюдения на базе стандарта **OpenTelemetry (OTel)**. Она объединяет распределенную трассировку и сбор метрик в единый поток данных, обеспечивая полную прозрачность работы оркестратора и воркеров.

---

## 1. Основные принципы

Система построена на трех китах:

1.  **Унификация**: Использование протокола **OTLP** для передачи и метрик, и трасс.
2.  **Сквозной контекст**: Проброс `trace_id` через сообщения протокола RXON от API до конечного воркера.
3.  **Опциональность**: Телеметрия не требует обязательной установки SDK. Если пакеты `opentelemetry-*` отсутствуют, система использует легковесные заглушки (No-Op), не влияя на производительность.

---

## 2. Распределенная трассировка (Tracing)

Трассировка позволяет отследить полный путь выполнения задачи.

### Структура спанов

- **`rxon_message:{type}`**: Создается в `engine.py` при получении любого сообщения от воркера (`poll`, `result`, `heartbeat`).
  - _Атрибуты_: `worker.id_hint`, `message.type`, `auth.worker_id`.
- **`JobExecutor:{blueprint}:{state}`**: Создается при выполнении шага логики в оркестраторе.
  - _Атрибуты_: `job.id`, `job.blueprint`, `job.client_token`, `job.retry_count`.
  - _События_: Ошибки записываются через `span.record_exception(e)` с полным стек-трейсом.

### Проброс контекста

Оркестратор извлекает контекст из HTTP-заголовков API-запроса и сохраняет его в поле `tracing_context` объекта задачи. Этот контекст передается воркеру в метаданных задачи, что позволяет объединить работу оркестратора и внешнего воркера в одну трассу.

---

## 3. Метрики (Metrics)

Метрики собираются в реальном времени и отправляются в коллектор по протоколу OTLP.

### Ключевые показатели

| Метрика                                       | Тип       | Описание                                                          |
| :-------------------------------------------- | :-------- | :---------------------------------------------------------------- |
| `orchestrator_jobs_total`                     | Counter   | Общее количество созданных задач (по блюпринтам).                 |
| `orchestrator_jobs_failed_total`              | Counter   | Количество упавших задач.                                         |
| `orchestrator_job_duration_seconds`           | Histogram | Распределение времени выполнения задач (P95/P99).                 |
| `orchestrator_task_queue_length`              | Gauge     | Текущее количество задач в очереди Redis.                         |
| `orchestrator_active_workers`                 | Gauge     | Количество активных воркеров в сети.                              |
| `orchestrator_loop_lag_seconds`               | Gauge     | Задержка событийного цикла asyncio (индикатор перегрузки CPU).    |
| `orchestrator_ratelimit_blocked_total`        | Counter   | Количество запросов, заблокированных лимитером.                   |
| `orchestrator_jobs_timeouts_total`            | Counter   | Количество задач, завершившихся по таймауту.                      |
| `orchestrator_tasks_ignored_total`            | Counter   | Количество игнорируемых результатов (запоздавшие или отмененные). |
| `orchestrator_tasks_hot_dispatched_total`     | Counter   | Задачи, отправленные на HOT-воркеры (с прогретым кешем).          |
| `orchestrator_s3_operations_total`            | Counter   | Общее количество операций с S3 хранилищем.                        |
| `orchestrator_s3_operation_duration_seconds`  | Histogram | Длительность операций S3.                                         |
| `orchestrator_scheduler_jobs_triggered_total` | Counter   | Количество задач, запущенных планировщиком.                       |

### Метрики безопасности (Zero Trust)

| Метрика                                         | Тип     | Описание                                                   |
| :---------------------------------------------- | :------ | :--------------------------------------------------------- |
| `orchestrator_security_auth_failures_total`     | Counter | Общее количество неудачных попыток аутентификации.         |
| `orchestrator_security_replay_detected_total`   | Counter | Количество обнаруженных атак повтора (истекший timestamp). |
| `orchestrator_security_identity_mismatch_total` | Counter | Несоответствия между CN сертификата и ID воркера.          |

---

## 4. Настройка и запуск

Телеметрия включается автоматически при наличии необходимых библиотек и настроек окружения.

### Переменные окружения

- `OTEL_EXPORTER_OTLP_ENDPOINT`: URL вашего OTel Collector или Jaeger (например, `http://localhost:4317`).
- `OTEL_SERVICE_NAME`: Имя сервиса (по умолчанию `avtomatika`).
- **Автоматические атрибуты**: Оркестратор автоматически добавляет `service.version` (из метаданных пакета) к каждой трассе и метрике.
- `LOG_LEVEL`: Установите `DEBUG` для отладки экспорта телеметрии.

### Пример запуска с Jaeger (локально)

1. Запустите Jaeger через Docker:

```bash
docker run -d --name jaeger \
  -e COLLECTOR_OTLP_ENABLED=true \
  -p 16686:16686 \
  -p 4317:4317 \
  jaegertracing/all-in-one:latest
```

2. Запустите оркестратор с указанием эндпоинта:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
pip install "avtomatika[telemetry]"
python -m avtomatika.engine
```

Теперь все трассы будут доступны в интерфейсе Jaeger по адресу `http://localhost:16686`.

---

## 5. Использование в коде (для разработчиков)

Если логика вашего блюпринта содержит сложные вычисления, работу с внешними БД или API, вы можете детализировать трассировку, создавая кастомные под-спаны.

### Почему это важно?

Автоматический спан `JobExecutor` показывает общее время выполнения шага. Кастомные спаны позволяют увидеть, сколько времени заняла конкретная операция (например, `Data:Fetch` или `LLM:Preprocessing`), что критично для оптимизации пайплайнов.

### Пример в обработчике блюпринта:

```python
from avtomatika.telemetry import trace

tracer = trace.get_tracer("my_blueprint")

@bp.handler
async def process_data(initial_data, actions):
    # 1. Создаем кастомный спан внутри шага
    with tracer.start_as_current_span("Internal:Transform") as span:
        span.set_attribute("data.size", len(initial_data))
        # ... ваша тяжелая логика трансформации ...
        result = {"status": "ok"}

    # 2. Основная логика оркестратора продолжается
    actions.go_to("next_step")
```

### Пример добавления метрики:

```python
from avtomatika import metrics

# В методе инициализации
meter = metrics.get_meter("avtomatika")
my_counter = meter.create_counter("custom_event_total")

# В коде
my_counter.add(1, {"type": "alert"})
```

---

_Примечание: Если `OTEL_EXPORTER_OTLP_ENDPOINT` не задан, данные будут выводиться в консоль (ConsoleExporter), что удобно для проверки корректности работы без развертывания Jaeger._
