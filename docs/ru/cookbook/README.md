[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/README.md) | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/cookbook/README.md) | **RU**

# **Кукбук: Рецепты для работы с библиотекой Оркестратора**

Эта книга рецептов содержит коллекцию примеров для создания рабочих процессов (блупринтов) с помощью Avtomatika Orchestrator.

> **Важно для Production:** В примерах ниже для простоты используется `print()`. В реальных приложениях рекомендуется использовать стандартный модуль `logging`. Оркестратор автоматически настраивает структурированное JSON-логирование, и использование `logging.info(...)` гарантирует, что ваши логи будут корректно отформатированы и сохранены.

### **Рецепт 1: Создание простого линейного пайплайна**

**Задача:** Создать пайплайн, который последовательно выполняет три шага: A -> B -> C.
```python
from orchestrator.blueprint import StateMachineBlueprint

simple_pipeline = StateMachineBlueprint(
    name="simple_linear_flow",
    api_version="v1",
    api_endpoint="jobs/simple_flow"
)

@simple_pipeline.handler_for("start", is_start=True)
async def start_handler(context, actions):
    actions.transition_to("step_A")

@simple_pipeline.handler_for("step_A")
async def handler_A(context, actions):
    actions.dispatch_task(
        task_type="simple_task",
        params=context.initial_data,
        transitions={"success": "step_B", "failure": "failed"}
    )

@simple_pipeline.handler_for("step_B")
async def handler_B(context, actions):
    actions.transition_to("finished")

@simple_pipeline.handler_for("finished", is_end=True)
async def finished_handler(context, actions):
    print(f"Пайплайн {context.job_id} успешно завершен.")

@simple_pipeline.handler_for("failed", is_end=True)
async def failed_handler(context, actions):
    print(f"Пайплайн {context.job_id} завершился с ошибкой.")
```

### **Рецепт 2: Реализация "Человек в середине" (модерация)**

**Задача:** После шага `generate_data` поставить пайплайн на паузу и дождаться одобрения от модератора.
```python
from orchestrator.blueprint import StateMachineBlueprint

moderation_pipeline = StateMachineBlueprint(
    name="moderation_flow",
    api_version="v1",
    api_endpoint="jobs/moderation_flow"
)

@moderation_pipeline.handler_for("generate_data", is_start=True)
async def generate_data_handler(context, actions):
    actions.dispatch_task(
        task_type="data_generation",
        params=context.initial_data,
        transitions={"success": "awaiting_approval", "failure": "failed"}
    )


@moderation_pipeline.handler_for("process_approved_data", is_end=True)
async def process_approved_handler(context, actions):
    actions.transition_to("finished")

@moderation_pipeline.handler_for("rejected_by_moderator", is_end=True)
async def process_rejected_handler(context, actions):
    print(f"Job {context.job_id} был отклонен модератором.")

@moderation_pipeline.handler_for("failed", is_end=True)
async def moderation_failed_handler(context, actions):
    print(f"Job {context.job_id} завершился с ошибкой.")

@moderation_pipeline.handler_for("awaiting_approval")
async def await_approval_handler(context, actions):
    actions.await_human_approval(
        integration="telegram",
        message=f"Требуется утверждение для Job {context.job_id}",
        transitions={
            "approved": "process_approved_data",
            "rejected": "rejected_by_moderator"
        }
    )

@moderation_pipeline.handler_for("process_approved_data")
async def process_approved_handler(context, actions):
    actions.transition_to("finished")


### **Рецепт 3: Параллельное выполнение и агрегация результатов**

**Задача:** Запустить несколько независимых задач (например, обработку разных частей одного файла) одновременно и, дождавшись завершения всех, собрать их результаты в один итоговый.

**Концепция:**
Параллелизм достигается путем многократного вызова `actions.dispatch_task()` в одном хендлере.
1.  **Запуск:** В обычном хендлере вы вызываете `dispatch_task` для каждой параллельной задачи. **Ключевое требование:** все эти вызовы должны указывать на одно и то же состояние в `transitions`. Это состояние и будет точкой сбора (агрегации).
2.  **Агрегация:** Хендлер для состояния-агрегатора помечается специальным декоратором `@blueprint.aggregator_for(...)`. Оркестратор не вызовет этот хендлер, пока **все** задачи, ведущие в это состояние, не будут завершены.
3.  **Доступ к результатам:** Внутри хендлера-агрегатора результаты всех параллельных задач доступны в `context.aggregation_results`. Это словарь, где ключ — это `task_id`, а значение — результат, возвращенный воркером.

```python
from orchestrator.blueprint import StateMachineBlueprint
import logging

logger = logging.getLogger(__name__)

parallel_bp = StateMachineBlueprint(
    name="parallel_flow_example",
    api_version="v1",
    api_endpoint="/jobs/parallel_example"
)

@parallel_bp.handler_for("start", is_start=True)
async def start_parallel_tasks(context, actions):
    """
    Этот хендлер запускает две задачи, которые будут выполняться параллельно.
    Обе задачи после успешного выполнения переведут процесс в состояние 'aggregate_results'.
    """
    logger.info(f"Job {context.job_id}: Запуск параллельных задач.")

    # Запускаем задачу A
    actions.dispatch_task(
        task_type="task_A",
        params={"input": "data_for_A"},
        transitions={"success": "aggregate_results", "failure": "failed"}
    )

    # Запускаем задачу B
    actions.dispatch_task(
        task_type="task_B",
        params={"input": "data_for_B"},
        transitions={"success": "aggregate_results", "failure": "failed"}
    )

@parallel_bp.aggregator_for("aggregate_results")
async def aggregate_results_handler(context, actions):
    """
    Этот хендлер будет вызван только после того, как и task_A, и task_B завершатся успешно.
    """
    logger.info(f"Job {context.job_id}: Агрегация результатов.")

    processed_results = {}
    # context.aggregation_results содержит результаты обеих задач
    for task_id, result in context.aggregation_results.items():
        if result.get("status") == "success":
            processed_results[task_id] = result.get("data")

    context.state_history["aggregated_data"] = processed_results
    logger.info(f"Job {context.job_id}: Агрегированные данные: {processed_results}")
    actions.transition_to("end")

@parallel_bp.handler_for("end", is_end=True)
async def end_flow(context, actions):
    final_data = context.state_history.get("aggregated_data")
    logger.info(f"Job {context.job_id}: Процесс завершен. Финальные данные: {final_data}")

@parallel_bp.handler_for("failed", is_end=True)
async def failed_handler(context, actions):
    logger.error(f"Job {context.job_id} завершился с ошибкой.")

```
*Примечание: Если хотя бы одна из параллельных задач завершится с ошибкой (перейдет в состояние `failed`), то хендлер-агрегатор не будет вызван, и весь `Job` сразу перейдет в состояние `failed`.*

### **Рецепт 4: Настройка Воркера для работы с несколькими Оркестраторами**

**Задача:** Обеспечить высокую доступность и/или распределить нагрузку, настроив один Воркер на подключение к нескольким экземплярам Оркестратора.

**Концепция:**
`worker_sdk` поддерживает два режима работы с несколькими Оркестраторами, которые настраиваются через переменные окружения. Воркер будет автоматически регистрироваться и отправлять heartbeats всем Оркестраторам из списка.

-   `FAILOVER` (по умолчанию): Воркер опрашивает Оркестраторы в порядке их появления в конфигурации. Если основной Оркестратор становится недоступен, Воркер автоматически переключается на следующий в списке.
-   `ROUND_ROBIN`: Воркер поочередно опрашивает каждый Оркестратор из списка, что позволяет распределять нагрузку между ними.

**Как настроить:**
1.  **`ORCHESTRATORS_CONFIG`**: Вместо `ORCHESTRATOR_URL` используйте эту переменную для передачи JSON-строки, описывающей все доступные Оркестраторы.
2.  **`MULTI_ORCHESTRATOR_MODE`**: Установите значение `FAILOVER` или `ROUND_ROBIN`.

#### **Пример 1: Настройка для отказоустойчивости (Failover)**

```bash
# Воркер будет опрашивать 'orchestrator-main'.
# Если он упадет, Воркер автоматически переключится на 'orchestrator-backup'.
export ORCHESTRATORS_CONFIG='[
    {"url": "http://orchestrator-main:8080"},
    {"url": "http://orchestrator-backup:8080"}
]'

export MULTI_ORCHESTRATOR_MODE="FAILOVER"

# Запускаем воркер
python -m your_worker_module
```

#### **Пример 2: Настройка для балансировки нагрузки (Round Robin)**
```bash
# Воркер будет поочередно опрашивать 'orchestrator-1' и 'orchestrator-2'.
export ORCHESTRATORS_CONFIG='[
    {"url": "http://orchestrator-1:8080"},
    {"url": "http://orchestrator-2:8080"}
]'

export MULTI_ORCHESTRATOR_MODE="ROUND_ROBIN"

# Запускаем воркер
python -m your_worker_module
```
*Примечание: Эта конфигурация выполняется исключительно на стороне Воркера и полностью прозрачна для Оркестратора. Каждый Оркестратор видит этого Воркера как обычного, зарегистрированного исполнителя.*

### **Рецепт 5: Условная маршрутизация с .when()**

```python
from orchestrator.blueprint import StateMachineBlueprint

multilingual_pipeline = StateMachineBlueprint(
    name="multilingual_flow",
    api_version="v1",
    api_endpoint="jobs/multilingual_flow"
)

@multilingual_pipeline.handler_for("start", is_start=True)
async def start_multilingual(context, actions):
    # Этот шаг просто передает управление дальше, где сработает условная логика
    actions.transition_to("process_text")

@multilingual_pipeline.handler_for("process_text").when("context.initial_data.language == 'en'")
async def process_english_text(context, actions):
    actions.dispatch_task(task_type="process_en", params=context.initial_data, transitions={"success": "finished"})

@multilingual_pipeline.handler_for("process_text").when("context.initial_data.language == 'de'")
async def process_german_text(context, actions):
    actions.dispatch_task(task_type="process_de", params=context.initial_data, transitions={"success": "finished"})

@multilingual_pipeline.handler_for("finished", is_end=True)
async def multilingual_finished(context, actions):
    print(f"Job {context.job_id} finished processing.")
```

### **Рецепт 6: Выбор стратегии распределения задач**

**Задача:** Для критически важной задачи использовать не просто случайного воркера, а наименее загруженного.

```python
from orchestrator.blueprint import StateMachineBlueprint

critical_pipeline = StateMachineBlueprint(
    name="critical_flow",
    api_version="v1",
    api_endpoint="jobs/critical_flow"
)

@critical_pipeline.handler_for("start", is_start=True)
async def start_critical_task(context, actions):
    actions.dispatch_task(
        task_type="critical_computation",
        params=context.initial_data,
        # Указываем стратегию выбора воркера
        dispatch_strategy="least_connections",
        transitions={"success": "finished"}
    )

@critical_pipeline.handler_for("finished", is_end=True)
async def critical_finished(context, actions):
    print(f"Critical task {context.job_id} finished.")
```
*Примечание: Доступные стратегии: `default`, `round_robin`, `least_connections`.*

### **Рецепт 22: Управление приоритетом задач**

**Задача:** В системе есть обычные задачи и срочные, которые должны выполняться в первую очередь, даже если они поступили позже.

**Решение:** Метод `dispatch_task` принимает параметр `priority`, который является числом с плавающей точкой. Чем выше значение, тем выше приоритет.

```python
from orchestrator.blueprint import StateMachineBlueprint

priority_pipeline = StateMachineBlueprint(
    name="priority_flow",
    api_version="v1",
    api_endpoint="jobs/priority_flow"
)

@priority_pipeline.handler_for("start", is_start=True)
async def start_priority_task(context, actions):
    # В зависимости от входных данных, назначаем разный приоритет
    is_urgent = context.initial_data.get("is_urgent", False)

    actions.dispatch_task(
        task_type="computation",
        params=context.initial_data,
        # Срочные задачи получают приоритет 10, остальные - 0
        priority=10.0 if is_urgent else 0.0,
        transitions={"success": "finished"}
    )

@priority_pipeline.handler_for("finished", is_end=True)
async def priority_finished(context, actions):
    print(f"Task {context.job_id} finished.")

```
*Примечание: Если несколько задач имеют одинаковый приоритет, они будут выполняться в порядке поступления (FIFO) в рамках этого приоритета.*


### **Рецепт 22: Оптимизация затрат с помощью `cheapest` и `max_cost`**

**Задача:** Отправить задачу на самый дешевый воркер, но только если его стоимость не превышает определенный порог.

```python
from orchestrator.blueprint import StateMachineBlueprint

cost_optimized_pipeline = StateMachineBlueprint(
    name="cost_optimized_flow",
    api_version="v1",
    api_endpoint="jobs/cost_optimized_flow"
)

@cost_optimized_pipeline.handler_for("start", is_start=True)
async def start_cost_optimized_task(context, actions):
    actions.dispatch_task(
        task_type="image_compression",
        params=context.initial_data,
        # Выбираем самый дешевый воркер
        dispatch_strategy="cheapest",
        # Но только если его стоимость ($/сек) не более 0.05
        max_cost=0.05,
        transitions={"success": "finished", "failure": "too_expensive"}
    )

@cost_optimized_pipeline.handler_for("finished", is_end=True)
async def cost_optimized_finished(context, actions):
    print("Job finished with cost optimization.")

@cost_optimized_pipeline.handler_for("too_expensive", is_end=True)
async def cost_optimized_failed(context, actions):
    print("Job failed because no workers met the cost criteria.")
```
*Примечание: Стратегия `cheapest` использует поле `cost_per_second` воркера. Если ни один воркер не соответствует `max_cost`, пайплайн не сможет найти исполнителя и завершится с ошибкой.*

### **Рецепт 22: Использование Хранилищ Данных (`data_stores`)**

**Задача:** Использовать разделяемое, персистентное хранилище (`data_store`) для обмена данными между разными состояниями или даже разными запусками одного и того же пайплайна. Например, для реализации счетчика или кэша.

**Концепция:**
1.  **Инициализация:** При создании блупринта вы можете "прикрепить" к нему одно или несколько хранилищ данных. Каждое хранилище — это, по сути, обертка над Redis, предоставляющая key-value доступ.
2.  **Доступ в хендлерах:** Внутри любого хендлера этого блупринта вы можете получить доступ к этим хранилищам через `context.data_stores`.
3.  **Персистентность:** Данные в `data_store` сохраняются между вызовами хендлеров и даже между разными `job_id` одного и того же блупринта.

```python
from orchestrator.blueprint import StateMachineBlueprint

# 1. Создаем блупринт и добавляем к нему data_store с именем 'request_counter'.
#    Мы также можем задать начальные значения.
analytics_bp = StateMachineBlueprint(
    name="analytics_flow",
    api_version="v1",
    api_endpoint="jobs/analytics"
)
analytics_bp.add_data_store("request_counter", {"total_requests": 0})


@analytics_bp.handler_for("start", is_start=True)
async def count_request(context, actions):
    # 2. Получаем доступ к хранилищу через context и увеличиваем счетчик.
    #    Операции атомарны, так как Redis однопоточен.
    current_count = await context.data_stores.request_counter.get("total_requests")
    new_count = current_count + 1
    await context.data_stores.request_counter.set("total_requests", new_count)

    # Сохраняем в историю этого конкретного job'а, какой был счетчик на момент его запуска
    context.state_history["request_number"] = new_count

    actions.transition_to("finished")

@analytics_bp.handler_for("finished", is_end=True)
async def show_result(context, actions):
    request_num = context.state_history.get("request_number")
    print(f"Job {context.job_id} был обработан. Это был запрос номер {request_num}.")

```

**Как это работает:**

-   `analytics_bp.add_data_store("request_counter", ...)` создает экземпляр `AsyncDictStore`, который будет жить, пока жив Оркестратор.
-   `context.data_stores.request_counter` предоставляет доступ к этому экземпляру. `data_stores` - это динамический объект, атрибуты которого соответствуют именам созданных хранилищ.
-   Каждый раз, когда вы запускаете этот пайплайн (`/v1/jobs/analytics`), он будет увеличивать **один и тот же** счетчик, потому что `data_store` привязан к блупринту, а не к конкретному `job_id`.

### **Рецепт 22: Отмена выполняемой задачи**

**Задача:** Запустить длительную задачу (например, обработку видео) и иметь возможность отменить ее выполнение через API.

**Концепция:**
Система поддерживает гибридный механизм отмены, который работает как с WebSocket, так и без него.
1.  **Запрос на отмену:** Вы отправляете `POST` запрос на API-эндпоинт `/api/v1/jobs/{job_id}/cancel`.
2.  **Установка флага:** Оркестратор немедленно устанавливает в Redis флаг, сигнализирующий о запросе на отмену.
3.  **Push-уведомление (WebSocket):** Если Воркер подключен по WebSocket, Оркестратор дополнительно отправляет ему команду `cancel_task` для немедленной реакции.
4.  **Pull-проверка (Redis):** Если Воркер не использует WebSocket или соединение временно разорвано, он должен периодически самостоятельно проверять наличие флага в Redis с помощью функции `worker.check_for_cancellation(task_id)`.
5.  **Реакция:** Получив сигнал отмены (любым из способов), Воркер должен прервать работу и вернуть результат со статусом `"cancelled"`.
6.  **Завершение:** Пайплайн переходит в состояние, указанное в `transitions` для статуса `"cancelled"`.

#### **Шаг 1: Код Воркера**
Воркер должен периодически вызывать `worker.check_for_cancellation`.

```python
# my_worker.py
@worker.task("long_video_processing")
async def process_video(params: dict, task_id: str, job_id: str) -> dict:
    total_frames = 1000
    for frame in range(total_frames):
        # ... логика обработки кадра ...
        await asyncio.sleep(0.1) # Имитация работы

        # Каждые 100 кадров проверяем, не пора ли остановиться
        if frame % 100 == 0:
            if await worker.check_for_cancellation(task_id):
                print(f"Задача {task_id} отменена.")
                return {"status": "cancelled"}

    return {"status": "success"}
```

#### **Шаг 2: Создание Блупринта**
Блупринт должен иметь переход для нового статуса `cancelled`.
```python
from orchestrator.blueprint import StateMachineBlueprint

cancellable_pipeline = StateMachineBlueprint(
    name="cancellable_flow",
    api_version="v1",
    api_endpoint="jobs/cancellable"
)

@cancellable_pipeline.handler_for("start", is_start=True)
async def start_long_task(context, actions):
    actions.dispatch_task(
        task_type="long_video_processing",
        params=context.initial_data,
        transitions={
            "success": "finished_successfully",
            "failure": "task_failed",
            "cancelled": "task_cancelled" # Новый переход
        }
    )

@cancellable_pipeline.handler_for("finished_successfully", is_end=True)
async def success_handler(context, actions):
    print(f"Job {context.job_id} успешно завершен.")

@cancellable_pipeline.handler_for("task_failed", is_end=True)
async def failure_handler(context, actions):
    print(f"Job {context.job_id} завершился с ошибкой.")

@cancellable_pipeline.handler_for("task_cancelled", is_end=True)
async def cancelled_handler(context, actions):
    print(f"Job {context.job_id} был успешно отменен.")
```

#### **Шаг 2: Запуск задачи**
```bash
# Запускаем Job и получаем его ID
curl -X POST http://localhost:8080/api/v1/jobs/cancellable \
-H "Content-Type: application/json" \
-H "X-Client-Token: your-secret-orchestrator-token" \
-d '{"video_url": "..."}'
# Ответ: {"status": "accepted", "job_id": "YOUR_JOB_ID"}
```

#### **Шаг 3: Отмена задачи**
```bash
# Отправляем запрос на отмену, используя полученный job_id
curl -X POST http://localhost:8080/api/v1/jobs/YOUR_JOB_ID/cancel \
-H "X-Client-Token: your-secret-orchestrator-token"
```
Вы увидите в логах Воркера сообщение об отмене, а `Job` в Оркестраторе перейдет в состояние `task_failed_or_cancelled`.

### **Рецепт 22: Отправка прогресса выполнения задачи**

**Задача:** Для длительной задачи (например, обучение модели) регулярно сообщать о прогрессе, чтобы его можно было отслеживать в UI.

**Концепция:**
Как и отмена, эта функция работает через WebSocket. Воркер может отправлять события с прогрессом, которые Оркестратор сохраняет в `state_history` соответствующего `Job`.

#### **Шаг 1: Код в Воркере**
Воркер должен периодически вызывать `worker.send_progress()`.
```python
# Внутри вашего файла воркера (my_worker.py)
@worker.task("train_model")
async def train_model_handler(params: dict, task_id: str, job_id: str) -> dict:
    for epoch in range(10):
        # ... логика обучения ...
        await asyncio.sleep(5)
        # Отправляем прогресс после каждой эпохи
        await worker.send_progress(
            task_id=task_id,
            job_id=job_id,
            progress=(epoch + 1) / 10,
            message=f"Epoch {epoch + 1} completed"
        )
    return {"status": "success"}


### **Рецепт 22a: Работа с S3 файлами в Оркестраторе (TaskFiles)**

**Задача:** Прочитать конфигурационный файл из S3 внутри обработчика Оркестратора для принятия решения о маршрутизации, или записать небольшой файл с результатом обратно в S3.

**Концепция:**
Когда поддержка S3 включена в Оркестраторе, вы можете запросить аргумент `task_files` в ваших обработчиках. Этот объект предоставляет вспомогательные методы для взаимодействия с папкой задачи в S3 (`jobs/{job_id}/`) без ручного управления соединениями.

**Предварительные требования:**
- Оркестратор настроен с переменными `S3_ENDPOINT_URL` и т.д.
- Установлены зависимости: `pip install avtomatika[s3]`

```python
from orchestrator.blueprint import StateMachineBlueprint

s3_ops_bp = StateMachineBlueprint(
    name="s3_ops_flow",
    api_version="v1",
    api_endpoint="jobs/s3_ops"
)

@s3_ops_bp.handler_for("check_config", is_start=True)
async def check_config_handler(context, task_files, actions):
    """
    Скачивает 'config.json' из S3 (jobs/{job_id}/config.json),
    читает его и принимает решение о следующем шаге.
    """
    if not task_files:
        # S3 может быть отключен в конфигурации
        actions.transition_to("failed")
        return

    try:
        # read_json автоматически скачивает файл, если его нет локально
        config = await task_files.read_json("config.json")
    except Exception:
        actions.transition_to("config_missing")
        return

    if config.get("mode") == "fast":
        actions.transition_to("fast_processing")
    else:
        actions.transition_to("deep_processing")

@s3_ops_bp.handler_for("fast_processing")
async def fast_process(context, task_files, actions):
    # ... логика ...
    
    # Записываем результат обратно в S3
    await task_files.write_text("result.txt", "Done fast.")
    
    # Или работаем с папками рекурсивно
    # await task_files.download("dataset/") # Скачает s3://.../dataset/ в локальную папку
    # await task_files.upload("output_folder") # Загрузит локальную папку в s3://.../output_folder/

    actions.transition_to("finished")
```

### **Рецепт 22b: Работа с большими файлами через S3 (Со стороны Воркера)**

**Задача:** Обработать большой видеофайл. Передавать его напрямую в JSON нецелесообразно, поэтому будем использовать S3 для обмена данными.

**Концепция:**
SDK для Воркеров имеет встроенную поддержку S3. Если в параметрах задачи (`params`) встречается значение, начинающееся с `s3://`, SDK автоматически скачает файл во временную директорию и подменит URI на локальный путь. Аналогично, если ваш обработчик возвращает локальный путь к файлу, SDK загрузит его в S3 и вернет Оркестратору `s3://` URI.

**Предварительные требования:**
- Установите зависимость `aioboto3`: `pip install orchestrator-worker[s3]`
- Настройте переменные окружения для доступа к S3:
  ```bash
  export S3_ENDPOINT_URL="http://your-s3-host:9000"
  export S3_ACCESS_KEY_ID="your-access-key"
  export S3_SECRET_ACCESS_KEY="your-secret-key"
  export S3_BUCKET_NAME="my-processing-bucket"
  ```

#### **Шаг 1: Код в Воркере**
Воркеру не нужно знать о S3. Он просто работает с локальными файлами.

```python
# my_video_worker.py
import os
from pathlib import Path

@worker.task("process_video_from_s3")
async def process_video(params: dict, **kwargs) -> dict:
    # SDK уже скачал файл и передал нам локальный путь
    local_video_path = Path(params["video_path"])

    if not local_video_path.exists():
        return {"status": "failure", "error_type": "INVALID_INPUT_ERROR", "error": "Input file not found"}

    # ... логика обработки видео ...
    # Создаем выходной файл
    output_path = local_video_path.parent / f"processed_{local_video_path.name}"
    with open(output_path, "w") as f:
        f.write("processed video data")

    # Просто возвращаем локальный путь. SDK сам загрузит его в S3.
    return {"status": "success", "data": {"processed_video_path": str(output_path)}}
```

#### **Шаг 2: Создание Блупринта**
Блупринт просто передает S3 URI как параметр.
```python
@s3_pipeline.handler_for("start", is_start=True)
async def start_s3_task(context, actions):
    actions.dispatch_task(
        task_type="process_video_from_s3",
        # Мы передаем S3 URI
        params={"video_path": context.initial_data.get("s3_uri")},
        transitions={"success": "finished"}
    )
```

#### **Шаг 3: Запуск задачи**
```bash
curl -X POST ... -d '{"s3_uri": "s3://my-bucket/raw_videos/movie.mp4"}'
```
После выполнения в `state_history` задачи появится результат с новым S3 URI, например: `{"processed_video_path": "s3://my-processing-bucket/processed_movie.mp4"}`.

### **Рецепт 22: Управление логикой повторов с помощью типов ошибок**

**Задача:** Создать воркер, который может различать временные сбои (например, недоступность внешнего API) и постоянные ошибки (например, неверный формат входных данных).

**Концепция:**
Оркестратор по умолчанию пытается повторить любую неудачную задачу (`TRANSIENT_ERROR`). Однако, если Воркер вернет ошибку с типом `PERMANENT_ERROR` или `INVALID_INPUT_ERROR`, Оркестратор не будет тратить время на повторные попытки.

- `PERMANENT_ERROR`: Задача немедленно перемещается в карантин для ручного анализа.
- `INVALID_INPUT_ERROR`: Задача немедленно помечается как `failed`.

#### **Шаг 1: Код в Воркере**
```python
# my_api_worker.py
@worker.task("fetch_external_data")
async def fetch_data(params: dict, **kwargs) -> dict:
    api_key = params.get("api_key")
    if not api_key:
        # Это невалидные входные данные, повторять бессмысленно
        return {"status": "failure", "error_type": "INVALID_INPUT_ERROR", "error": "API key is missing"}

    try:
        # ... попытка вызова внешнего API ...
        response = await call_flaky_api(api_key)
        return {"status": "success", "data": response}
    except APITimeoutError:
        # API временно недоступен, стоит попробовать еще раз
        return {"status": "failure", "error_type": "TRANSIENT_ERROR", "error": "API timed out"}
    except APIAuthError:
        # Ключ API неверный, это постоянная проблема
        return {"status": "failure", "error_type": "PERMANENT_ERROR", "error": "Invalid API key"}

```

#### **Шаг 2: Создание Блупринта**
Блупринт может иметь разные ветки для разных исходов.
```python
@error_handling_bp.handler_for("start", is_start=True)
async def start_api_call(context, actions):
    actions.dispatch_task(
        task_type="fetch_external_data",
        params=context.initial_data,
        transitions={
            "success": "finished_successfully",
            "failure": "handle_failure" # Общий обработчик для всех ошибок
        }
    )

@error_handling_bp.handler_for("handle_failure", is_end=True)
async def failure_handler(context, actions):
    # Здесь можно проанализировать `job_state`, чтобы понять,
    # была ли задача помещена в карантин или просто провалена.
    job_state = await context.storage.get_job_state(context.job_id)
    if job_state.get("status") == "quarantined":
        print(f"Job {context.job_id} quarantined due to a permanent error.")
    else:
        print(f"Job {context.job_id} failed.")
```
```

#### **Шаг 2: Проверка в Оркестраторе**
После выполнения задачи вы можете запросить ее статус и увидеть `progress_updates` в `state_history`:
```json
{
  "id": "YOUR_JOB_ID",
  "current_state": "finished",
  "state_history": {
    "progress_updates": [
      {"progress": 0.1, "message": "Epoch 1 completed", "timestamp": "..."},
      {"progress": 0.2, "message": "Epoch 2 completed", "timestamp": "..."}
    ]
  }
}
```

### **Рецепт 22: Вложенные Блупринты (Sub-blueprints)**

**Задача:** Создать основной пайплайн, который в качестве одного из шагов использует другой, переиспользуемый пайплайн, и получить результат его работы.

**Концепция:**
Механизм вложенных блупринтов позволяет одному пайплайну (родительскому) запускать другой (дочерний) и ожидать его завершения. Результат выполнения дочернего блупринта (успех или неудача) автоматически сохраняется в `state_history` родительского пайплайна. Это позволяет создавать сложные, но модульные и переиспользуемые рабочие процессы.

```python
from orchestrator.blueprint import StateMachineBlueprint

# 1. Сначала определяем маленький, переиспользуемый блупринт.
#    У него нет своего API-эндпоинта, он может быть запущен только из другого блупринта.
text_processing_bp = StateMachineBlueprint(name="text_processor")

@text_processing_bp.handler_for("start", is_start=True)
async def process(context, actions):
    # ... какая-то логика обработки текста ...
    text = context.initial_data.get("text", "")
    if not text:
        # Если входные данные некорректны, завершаем блупринт с ошибкой.
        actions.transition_to("failed")
    else:
        # В `state_history` дочернего блупринта можно сохранить результат.
        # Хотя он не передается напрямую, он будет доступен для отладки в истории.
        context.state_history["processed_text"] = text.upper()
        actions.transition_to("finished")

@text_processing_bp.handler_for("finished", is_end=True)
async def text_processing_finished(context, actions):
    pass

@text_processing_bp.handler_for("failed", is_end=True)
async def text_processing_failed(context, actions):
    pass


# 2. Теперь создаем основной пайплайн, который будет его вызывать.
main_pipeline = StateMachineBlueprint(
    name="main_flow",
    api_version="v1",
    api_endpoint="jobs/main_flow"
)

@main_pipeline.handler_for("start", is_start=True)
async def parent_start(context, actions):
    actions.transition_to("process_user_text")

@main_pipeline.handler_for("process_user_text")
async def main_handler(context, actions):
    user_text = context.initial_data.get("raw_text", "")
    actions.run_blueprint(
        blueprint_name="text_processor",
        initial_data={"text": user_text},
        transitions={"success": "final_step", "failure": "sub_job_failed"}
    )

@main_pipeline.handler_for("final_step")
async def final_step_handler(context, actions):
    # Результат выполнения дочернего блупринта сохраняется в `state_history`.
    # Ключ генерируется автоматически. Мы можем найти его, перебрав ключи.
    sub_job_result = None
    for key, value in context.state_history.items():
        if key.startswith("sub_job_") and "outcome" in value:
            sub_job_result = value
            break

    if sub_job_result:
        print(f"Sub-blueprint finished with outcome: {sub_job_result['outcome']}")
    else:
        print("Sub-blueprint result not found in state history.")

    actions.transition_to("finished")

@main_pipeline.handler_for("sub_job_failed")
async def sub_job_failed_handler(context, actions):
    print("Sub-blueprint failed, handling failure in parent.")
    actions.transition_to("failed")

@main_pipeline.handler_for("finished", is_end=True)
async def main_finished(context, actions):
    pass

@main_pipeline.handler_for("failed", is_end=True)
async def main_failed(context, actions):
    pass
```

### **Рецепт 22: Визуализация логики Блупринта**

**Задача:** Проанализировать или задокументировать сложный пайплайн, автоматически сгенерировав его визуальную схему.

**Решение:** Каждый объект `StateMachineBlueprint` имеет встроенный метод `.render_graph()`, который использует `graphviz` для создания диаграммы состояний и переходов. Он анализирует код ваших хендлеров, чтобы найти вызовы `actions.transition_to()` и `actions.dispatch_task()`, и строит граф на их основе.

**Предварительное требование:**
Для работы этой функции в вашей системе должен быть установлен **Graphviz**.
-   **Debian/Ubuntu:** `sudo apt-get install graphviz`
-   **macOS (Homebrew):** `brew install graphviz`
-   **Windows:** Установите с официального сайта и добавьте в `PATH`.

**Пример:**
```python
from orchestrator.blueprint import StateMachineBlueprint

# Возьмем пайплайн с условной логикой из другого рецепта
conditional_pipeline = StateMachineBlueprint(name="conditional_flow")

@conditional_pipeline.handler_for("start", is_start=True)
async def start(context, actions):
    actions.transition_to("process_data")

@conditional_pipeline.handler_for("process_data").when("context.initial_data.type == 'A'")
async def process_a(context, actions):
    actions.dispatch_task(task_type="task_a", transitions={"success": "finished", "failure": "failed"})

@conditional_pipeline.handler_for("process_data").when("context.initial_data.type == 'B'")
async def process_b(context, actions):
    actions.dispatch_task(task_type="task_b", transitions={"success": "finished", "failure": "failed"})

@conditional_pipeline.handler_for("finished", is_end=True)
async def finished(context, actions):
    print("Finished.")

@conditional_pipeline.handler_for("failed", is_end=True)
async def failed(context, actions):
    print("Failed.")

# Теперь сгенерируем его диаграмму
if __name__ == "__main__":
    # Эта команда создаст файл 'conditional_flow_diagram.png' в текущей директории
    conditional_pipeline.render_graph("conditional_flow_diagram", format="png")
```
Запуск этого скрипта создаст изображение, наглядно показывающее все возможные пути выполнения задания в этом блупринте.

### **Рецепт 22: Частичное обновление состояния Воркера (PATCH)**

**Задача:** Воркеру нужно обновить только одно поле в своем состоянии (например, текущую нагрузку), не отправляя все свои метаданные (ID, capabilities, cost и т.д.).

**Решение:** API Оркестратора поддерживает метод `PATCH` для эндпоинта `/_worker/workers/{worker_id}/status`, который идеально подходит для частичных обновлений. Это экономит трафик и соответствует лучшим практикам REST.

```python
# Пример кода на стороне воркера, который отправляет только изменившиеся данные
async def update_load(session, new_load):
    await session.patch(
        "http://orchestrator.host/_worker/workers/my-worker-id/status",
        json={"load": new_load}
    )
```

### **Рецепт 22: Управление квотами и конфигурацией клиентов**

**Задача:** Настроить для разных клиентов разные лимиты использования (квоты) и использовать их кастомные параметры внутри блупринтов.

**Концепция:**
1.  **Конфигурация:** Вся информация о клиентах, включая их токен, план и квоты, определяется в файле `clients.toml`.
2.  **Загрузка при старте:** При запуске Оркестратор считывает этот файл и загружает данные о квотах в Redis.
3.  **Проверка квоты:** Специальное Quota Middleware автоматически проверяет и уменьшает счетчик "попыток" для каждого запроса к API. Если попытки закончились, запрос будет отклонен с ошибкой `429 Too Many Requests`.
4.  **Доступ к параметрам:** Внутри хендлера блупринта вы можете получить доступ ко всем статическим параметрам клиента (например, его план или список языков) через объект `context.client.config`.

#### **Шаг 1: Настройка `clients.toml`**
```toml
[client_premium]
token = "user_token_vip"
plan = "premium"
monthly_attempts = 100000
languages = ["en", "de", "fr"]

[client_free]
token = "user_token_free"
plan = "free"
monthly_attempts = 100
languages = ["en"]
```

#### **Шаг 2: Использование в Блупринте**
```python
from orchestrator.blueprint import StateMachineBlueprint

premium_features_bp = StateMachineBlueprint(
    name="premium_flow",
    api_version="v1",
    api_endpoint="jobs/premium_flow"
)

@premium_features_bp.handler_for("start", is_start=True)
async def start_premium_flow(context, actions):
    # Получаем доступ к конфигурации клиента, сделавшего запрос
    client_config = context.client.config

    # Используем параметры клиента для условной логики
    if client_config.get("plan") == "premium":
        # Для премиум-клиентов используем более мощный воркер
        actions.dispatch_task(
            task_type="high_quality_generation",
            params=context.initial_data,
            transitions={"success": "finished"}
        )
    elif "en" in client_config.get("languages", []):
         # Для бесплатных англоговорящих клиентов - стандартный воркер
        actions.dispatch_task(
            task_type="standard_quality_generation",
            params=context.initial_data,
            transitions={"success": "finished"}
        )
    else:
        # Для остальных - ошибка
        actions.transition_to("failed", error_message="Language not supported for your plan")

@premium_features_bp.handler_for("finished", is_end=True)
async def premium_finished(context, actions):
    pass

@premium_features_bp.handler_for("failed", is_end=True)
async def premium_failed(context, actions):
    pass
```

### **Рецепт 22: Лучшие практики для `is_start` и `is_end` хендлеров**

**Задача:** Понять, какой код лучше всего размещать в начальных и конечных обработчиках для создания чистых и надежных пайплайнов.

#### **Назначение `is_start=True` хендлера**

Начальный хендлер — это "входные ворота" вашего пайплайна. Это идеальное место для:
1.  **Валидации и подготовки данных:** Проверьте, что все необходимые данные присутствуют, и подготовьте `state_history` для последующих шагов.
2.  **Начальной маршрутизации:** Примите решение о первом реальном шаге на основе входных данных.

**Пример:**
```python
from orchestrator.blueprint import StateMachineBlueprint

validation_bp = StateMachineBlueprint(
    name="validation_example",
    api_version="v1",
    api_endpoint="jobs/validation"
)

@validation_bp.handler_for("validate_input", is_start=True)
async def validate_input_handler(context, actions):
    """
    Проверяет входные данные и решает, куда направить процесс.
    """
    user_id = context.initial_data.get("user_id")
    document_type = context.initial_data.get("document_type")

    if not user_id or not document_type:
        actions.transition_to("invalid_input_failed")
        return

    # Сохраняем проверенные данные в state_history для использования в других хендлерах
    context.state_history["validated_user"] = user_id
    context.state_history["doc_type"] = document_type

    # Маршрутизация на основе типа документа
    if document_type == "invoice":
        actions.transition_to("process_invoice")
    else:
        actions.transition_to("process_other_document")

# ... (другие хендлеры) ...

@validation_bp.handler_for("invalid_input_failed", is_end=True)
async def invalid_input_handler(context, actions):
    print(f"Job {context.job_id} failed due to invalid input.")

```

#### **Назначение `is_end=True` хендлеров**

Конечные хендлеры — это "выход" из вашего пайплайна. Они выполняют финальные действия и **не должны** содержать вызовов `actions.transition_to()` или `dispatch_task()`.

**Ключевые использования:**
1.  **Финальное логирование и уведомления:** Записать итог работы, отправить email или сообщение в Slack.
2.  **Очистка ресурсов:** Удалить временные файлы, созданные в процессе работы.
3.  **Обработка разных исходов:** Вы можете иметь несколько конечных состояний для разных результатов (успех, ошибка, отклонение и т.д.).

**Пример:**
```python
from orchestrator.blueprint import StateMachineBlueprint
import os

finalization_bp = StateMachineBlueprint(name="finalization_example")

# ... (промежуточные шаги, которые могут привести к разным исходам) ...

@finalization_bp.handler_for("cleanup_and_notify_success", is_end=True)
async def success_handler(context, actions):
    """
    Выполняется при успешном завершении.
    """
    temp_file = context.state_history.get("temp_file_path")
    if temp_file and os.path.exists(temp_file):
        os.remove(temp_file)
        print(f"Job {context.job_id}: Temporary file {temp_file} deleted.")

    # send_success_email(context.initial_data.get("user_email"))
    print(f"Job {context.job_id} finished successfully. Notification sent.")


@finalization_bp.handler_for("handle_rejection", is_end=True)
async def rejection_handler(context, actions):
    """
    Выполняется, если процесс был отклонен.
    """
    rejection_reason = context.state_history.get("rejection_reason", "No reason provided")
    print(f"Job {context.job_id} was rejected. Reason: {rejection_reason}")
    # send_rejection_notification(context.initial_data.get("user_email"), rejection_reason)

```
Использование `is_start` и `is_end` таким образом делает ваши пайплайны более структурированными, надежными и легкими для понимания.

### **Рецепт 22: Маршрутизация задач на основе требований к ресурсам**

**Задача:** Для задачи генерации видео (`video_montage`) требуется мощная видеокарта. Необходимо гарантировать, что задача будет отправлена на воркер, оснащенный как минимум NVIDIA T4.

**Решение:** Метод `dispatch_task` в `ActionFactory` принимает параметр `resource_requirements`, который позволяет указывать минимальные требования к ресурсам воркера. Диспетчер автоматически отфильтрует воркеры, которые не соответствуют этим требованиям.

```python
from orchestrator.blueprint import StateMachineBlueprint

gpu_intensive_pipeline = StateMachineBlueprint(
    name="gpu_intensive_flow",
    api_version="v1",
    api_endpoint="jobs/gpu_flow"
)

@gpu_intensive_pipeline.handler_for("start")
async def start_gpu_task(context, actions):
    actions.dispatch_task(
        task_type="video_montage",
        params=context.initial_data,
        # Диспетчер будет искать воркер, у которого в поле `gpu_info.model`
        # содержится строка "NVIDIA T4", а также есть нужная модель
        resource_requirements={
            "gpu": {
                "model": "NVIDIA T4",
                "vram_gb": 16
            },
            "installed_models": [
                "stable-diffusion-1.5"
            ]
        },
        transitions={"success": "finished", "failure": "failed"}
    )
```

### **Рецепт 22: Сквозная трассировка с OpenTelemetry**

**Задача:** Отследить полный путь выполнения задания, от его создания до обработки на воркере и завершения.

**Решение:** Оркестратор автоматически управляет контекстом трассировки OpenTelemetry. Контекст создается при получении API-запроса, передается воркеру вместе с задачей, а затем возвращается обратно с результатом. Это позволяет объединить все операции в единую трассу.

**Как это работает:**
1.  **Начало трассы:** При вызове `POST /api/...` Оркестратор создает корневой спан для нового задания.
2.  **Оркестратор -> Воркер:** При вызове `actions.dispatch_task(...)`, `Dispatcher` автоматически инжектирует W3C Trace Context в заголовки HTTP-запроса к воркеру.
3.  **Воркер:** Эмулятор воркера извлекает контекст из заголовков и создает дочерний спан на время выполнения задачи.
4.  **Воркер -> Оркестратор:** При отправке результата воркер инжектирует контекст своего спана в заголовки callback-запроса.
5.  **Завершение трассы:** Оркестратор получает результат, извлекает контекст и продолжает трассу.

Чтобы увидеть результат, вам понадобится настроенный коллектор OpenTelemetry и бэкенд для визуализации (например, Jaeger или Zipkin). В консоли вы увидите только предупреждение о том, что экспортер не настроен.

---

### **Рецепты для Хранилища Истории (History Storage)**

Этот раздел содержит полезные примеры кода для прямой работы с базами данных, которые могут понадобиться при реализации или расширении функционала хранения истории.

#### **Рецепт 22: Прямая работа с SQLite**

**Задача:** Подключиться к файлу базы данных SQLite, создать таблицу и вставить данные, используя стандартную библиотеку `sqlite3`.

```python
import sqlite3
import json

# Подключаемся к файлу БД (он будет создан, если не существует)
con = sqlite3.connect("history.db")
cur = con.cursor()

# Создаем таблицу
cur.execute("""
    CREATE TABLE IF NOT EXISTS job_history (
        event_id TEXT PRIMARY KEY,
        job_id TEXT,
        timestamp TEXT,
        data JSON
    )
""")

# Готовим данные
event_data = {"status": "completed", "result": "ok"}

# Вставляем запись, используя json.dumps для преобразования dict в строку
cur.execute(
    "INSERT INTO job_history VALUES (?, ?, ?, ?)",
    ("evt_123", "job_abc", "2024-01-01T12:00:00Z", json.dumps(event_data))
)

# Сохраняем изменения
con.commit()

# Читаем данные и преобразуем JSON-строку обратно в dict
res = cur.execute("SELECT job_id, data FROM job_history WHERE event_id = 'evt_123'")
job_id, raw_data = res.fetchone()
retrieved_data = json.loads(raw_data)

print(f"Job ID: {job_id}, Data: {retrieved_data}")

con.close()
```
*Примечание: SQLite нативно поддерживает тип данных JSON, что упрощает хранение сложных вложенных структур.*


#### **Рецепт 22: Асинхронная работа с PostgreSQL**

**Задача:** Асинхронно подключиться к PostgreSQL, создать таблицу и вставить данные, используя библиотеку `asyncpg`.

**Предварительная установка:** `pip install asyncpg`

```python
import asyncio
import asyncpg
import json

async def main():
    # Подключаемся к PostgreSQL
    conn = await asyncpg.connect(user='user', password='password',
                                 database='db', host='127.0.0.1')

    # Создаем таблицу, используя нативный тип JSONB для эффективности
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS job_history (
            event_id TEXT PRIMARY KEY,
            job_id TEXT,
            timestamp TIMESTAMPTZ,
            data JSONB
        )
    """)

    # Готовим данные
    event_data = {"status": "dispatched", "worker": "worker-007"}

    # Вставляем запись. asyncpg автоматически кодирует dict в JSONB.
    await conn.execute(
        "INSERT INTO job_history (event_id, job_id, timestamp, data) VALUES ($1, $2, NOW(), $3)",
        "evt_456", "job_xyz", event_data
    )

    # Читаем данные. asyncpg автоматически декодирует JSONB в dict.
    row = await conn.fetchrow(
        "SELECT job_id, data FROM job_history WHERE event_id = $1",
        "evt_456"
    )
    print(f"Job ID: {row['job_id']}, Data: {row['data']}")

    # Закрываем соединение
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
```
*Примечание: Использование `JSONB` в PostgreSQL предпочтительнее, чем `JSON`, так как он хранится в бинарном формате и позволяет создавать индексы по ключам внутри JSON-документа.*

---

#### **Рецепт 22: Включение и использование истории выполнения**

**Задача:** Включить запись детальной истории выполнения задач и получить её через API для анализа.

**Шаг 1: Включение хранилища истории**

История выполнения по умолчанию отключена. Чтобы ее включить, необходимо задать переменную окружения `HISTORY_DATABASE_URI`.

*   **Для использования SQLite:**
    ```bash
    export HISTORY_DATABASE_URI="sqlite:path/to/your_history.db"
    ```

*   **Для использования PostgreSQL:**
    ```bash
    export HISTORY_DATABASE_URI="postgresql://user:password@hostname/dbname"
    ```

После установки этой переменной Оркестратор автоматически начнет записывать события в указанную базу данных.

**Шаг 2: Получение истории через API**

Когда история включена, становится доступен новый эндпоинт для ее получения.

*   **Запрос:**
    ```bash
    curl http://localhost:8080/api/jobs/{job_id}/history -H "X-Client-Token: your_token"
    ```

*   **Пример ответа:**
    ```json
    [
        {
            "event_id": "a1b2c3d4-...",
            "job_id": "job_123",
            "timestamp": "2024-08-27T10:00:00.123Z",
            "state": "start",
            "event_type": "state_started",
            "duration_ms": null,
            "context_snapshot": { "... (полное состояние задачи на момент начала) ..." }
        },
        {
            "event_id": "e5f6g7h8-...",
            "job_id": "job_123",
            "timestamp": "2024-08-27T10:00:01.456Z",
            "state": "start",
            "event_type": "state_finished",
            "duration_ms": 1333,
            "next_state": "processing",
            "context_snapshot": { "... (состояние задачи после выполнения хендлера) ..." }
        }
    ]
    ```
Этот эндпоинт позволяет получить полную, пошаговую хронологию выполнения любой задачи для детального анализа и отладки.

---

### **Рецепт 22: Аутентификация с индивидуальным токеном**

**Задача:** Повысить безопасность, настроив для каждого воркера уникальный токен аутентификации вместо использования одного общего секрета.

**Концепция:**
Система поддерживает гибридную модель аутентификации. Приоритет отдается индивидуальному токену, привязанному к `worker_id`. Если он не найден, система для обратной совместимости проверяет общий `WORKER_TOKEN`.

#### **Шаг 1: Настройка Оркестратора**
Определите индивидуальные токены в файле `workers.toml` в корневой директории Оркестратора.

```toml
# workers.toml
[worker-001]
token = "super-secret-token-for-worker-1"
# ... другие метаданные для этого воркера ...

[worker-002]
token = "another-unique-token-for-worker-2"
```
При запуске Оркестратор загрузит эти токены в Redis.

#### **Шаг 2: Настройка Воркера**
Воркер должен передавать свой ID и уникальный токен. Настройте для него следующие переменные окружения:

```bash
# Уникальный идентификатор, который должен совпадать с ключом в workers.toml
export WORKER_ID="worker-001"
# Индивидуальный токен для этого воркера
export WORKER_INDIVIDUAL_TOKEN="super-secret-token-for-worker-1"

# Общий WORKER_TOKEN больше не нужен, если используется индивидуальный
```

#### **Шаг 3: Запуск**
Запустите Оркестратор и Воркер. Воркер аутентифицируется, используя свой `WORKER_ID` и `WORKER_INDIVIDUAL_TOKEN`. Запросы от воркеров с неверным токеном или от воркеров, не перечисленных в `workers.toml` (если не настроен общий `WORKER_TOKEN`), будут отклонены с ошибкой `401 Unauthorized`.
