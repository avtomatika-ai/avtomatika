[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/creating_blueprint.md) | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/cookbook/creating_blueprint.md) | **RU**

# Cookbook: Создание Блупринта (Пайплайна)

Блупринты (`StateMachineBlueprint`) — это основа для определения бизнес-логики в системе. Каждый блупринт представляет собой конечный автомат ("сценарий"), который Оркестратор будет выполнять.

Это руководство покажет, как создать простой, но полноценный пайплайн.

## Шаг 1: Создайте файл для блупринта

Рекомендуется хранить блупринты в отдельном файле, например, `my_service/blueprints.py`.

## Шаг 2: Определите блупринт

Импортируйте `StateMachineBlueprint` и создайте его экземпляр.

- `name`: Уникальное имя блупринта.
- `api_version`: Версия API (например, "v1").
- `api_endpoint`: URL, по которому клиенты будут создавать задачи для этого пайплайна.

```python
from avtomatika import StateMachineBlueprint

# Создаем экземпляр блупринта
order_pipeline = StateMachineBlueprint(
    name="order_processing_flow",
    api_version="v1",
    api_endpoint="/jobs/process_order"  # URL для создания задач
)
```

## Шаг 3: Определите состояния и обработчики

Каждый шаг в вашем процессе — это "состояние" с привязанной к нему функцией-"обработчиком".

-   Декоратор `@blueprint.handler_for("state_name")` связывает функцию с состоянием.
-   **Начальное состояние** должно быть ровно одно, оно помечается флагом `is_start=True`.
-   **Конечных состояний** может быть несколько, они помечаются `is_end=True`.

Обработчик получает один аргумент — `context`, который содержит всю информацию о задаче и методы для управления процессом (`context.actions`).

```python
@order_pipeline.handler_for("start", is_start=True)
async def start_handler(context):
    """
    Начальный обработчик. Вызывается при создании Job.
    """
    print(f"Job {context.job_id}: обработка заказа начата.")
    print(f"Входные данные: {context.initial_data}")

    # Сохраняем что-то в историю для следующих шагов
    context.state_history["processed_by"] = "start_handler"

    # Переходим к следующему шагу
    context.actions.transition_to("dispatch_to_worker")


@order_pipeline.handler_for("dispatch_to_worker")
async def dispatch_handler(context):
    """
    Этот обработчик отправляет задачу на выполнение воркеру.
    """
    print(f"Job {context.job_id}: отправка задачи 'check_inventory' воркеру.")

    # Приостанавливаем пайплайн и ждем результат от воркера
    context.actions.dispatch_task(
        task_type="check_inventory",  # Тип задачи, который поймет воркер
        params={"items": context.initial_data.get("items")},
        
        # Определяем, куда пойдет процесс в зависимости от ответа воркера
        transitions={
            "success": "inventory_ok",      # Если воркер вернет status="success"
            "out_of_stock": "inventory_failed", # Если воркер вернет кастомный статус
            "failure": "generic_failure"    # Если воркер вернет status="failure"
        }
    )

@order_pipeline.handler_for("inventory_ok")
async def inventory_ok_handler(context):
    """
    Вызывается, если воркер подтвердил наличие товара.
    """
    # Данные, возвращенные воркером, доступны в state_history
    worker_data = context.state_history.get("warehouse_info")
    print(f"Job {context.job_id}: товар в наличии. Информация от воркера: {worker_data}")
    
    context.actions.transition_to("finished_successfully")


@order_pipeline.handler_for("inventory_failed", is_end=True)
async def inventory_failed_handler(context):
    """
    Конечное состояние, если товара нет в наличии.
    """
    print(f"Job {context.job_id}: не удалось обработать заказ, товара нет на складе.")


@order_pipeline.handler_for("generic_failure", is_end=True)
async def failed_handler(context):
    """
    Конечное состояние для обработки общих сбоев.
    """
    print(f"Job {context.job_id}: произошла ошибка при обработке.")


@order_pipeline.handler_for("finished_successfully", is_end=True)
async def finished_handler(context):
    """
    Конечное состояние при успешном выполнении.
    """
    print(f"Job {context.job_id}: заказ успешно обработан!")

```

## Шаг 4: Регистрация Блупринта

В главном файле вашего приложения, где вы запускаете `OrchestratorEngine`, зарегистрируйте созданный блупринт.

Когда вы вызываете `register_blueprint()`, движок автоматически выполняет **проверку целостности**. Он гарантирует, что:
1.  У блупринта есть ровно одно начальное состояние.
2.  Все переходы ведут к существующим состояниям (нет "висячих" ссылок).
3.  Все состояния достижимы из начального состояния (нет "мертвого кода").

Если любая из этих проверок не пройдет, будет выброшено исключение `ValueError`, предотвращающее запуск приложения со сломанной конфигурацией.

```python
# main.py
from avtomatika import OrchestratorEngine
from my_service.blueprints import order_pipeline  # Импортируем наш блупринт

# ... (настройка storage и config)

engine = OrchestratorEngine(storage, config)

# Регистрация блупринта в движке (Запускает валидацию!)
engine.register_blueprint(order_pipeline)

# Запуск движка
engine.run()
```

После этого вы сможете создавать задачи, отправляя POST-запросы на `/api/v1/jobs/process_order`.
