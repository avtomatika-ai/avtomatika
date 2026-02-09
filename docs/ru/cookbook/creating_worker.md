[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/creating_worker.md) | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/cookbook/creating_worker.md) | **RU**

# Cookbook: Создание Воркера

Воркеры — это независимые исполнители, которые выполняют реальную работу. Это руководство покажет, как создать воркер с помощью пакета `avtomatika-worker`.

## Шаг 1: Установка SDK

Убедитесь, что SDK установлен в вашем окружении.
```bash
pip install avtomatika-worker
```

## Шаг 2: Создание файла Воркера

Создайте Python-файл (например, `my_worker.py`) и импортируйте класс `Worker`.

```python
import asyncio
from avtomatika_worker import Worker
from avtomatika_worker.typing import TRANSIENT_ERROR

# 1. Инициализируйте класс Worker
# Укажите уникальный тип вашего воркера.
worker = Worker(worker_type="inventory-checker")

# 2. Определите обработчики задач с помощью декоратора @worker.task
@worker.task("check_inventory")
async def check_inventory_handler(params: dict, **kwargs) -> dict:
    """
    Эта функция будет вызвана, когда Оркестратор отправит
    задачу типа "check_inventory".

    - `params` (dict): Параметры для выполнения задачи.
    - `**kwargs`: Метаданные задачи:
        - `task_id` (str): Уникальный ID задачи.
        - `job_id` (str): ID родительского Job.
        - `priority` (float): Приоритет задачи.
    """
    print(f"Получены параметры: {params}")
    items = params.get("items", [])

    # Имитация работы: проверка наличия товаров
    await asyncio.sleep(1)

    if "unavailable_item" in items:
        # Возвращаем кастомный статус, который обработает блупринт
        return {
            "status": "out_of_stock",
            "data": {"missing_item": "unavailable_item"}
        }

    # 3. Верните успешный результат
    #    - 'status': "success" или кастомный статус.
    #    - 'data': Словарь с данными, которые будут добавлены в контекст Job.
    return {
        "status": "success",
        "data": {"warehouse_info": "All items are available"}
    }

# Пример обработчика для долгой задачи с кооперативной отменой
@worker.task("long_running_task")
async def long_task_handler(params: dict, **kwargs) -> dict:
    task_id = kwargs["task_id"]
    print(f"Запуск долгой задачи {task_id}...")
    
    for i in range(10):
        # Проверяем, не запросил ли Оркестратор отмену
        if await worker.check_for_cancellation(task_id):
            print(f"Отмена задачи {task_id} обнаружена. Завершаем...")
            return {"status": "cancelled", "message": "Task was cancelled by user."}
        
        print(f"Шаг {i+1}/10 выполнен...")
        await asyncio.sleep(2)

    return {"status": "success"}


# 4. Запустите воркер
if __name__ == "__main__":
    worker.run()
```

## Шаг 3: Настройка подключения и аутентификации

Создайте файл `.env` в той же директории, где находится ваш `my_worker.py`, или экспортируйте переменные в окружение.

```dotenv
# Уникальный ID этого экземпляра воркера
WORKER_ID=inventory-worker-01

# Адрес вашего Оркестратора
ORCHESTRATOR_URL=http://localhost:8080

# Токен для аутентификации воркера. Должен совпадать с токеном,
# который ожидает Оркестратор (глобальным или индивидуальным).
WORKER_TOKEN=your-secret-worker-token

# (Опционально) Включить WebSocket для мгновенной отмены задач
WORKER_ENABLE_WEBSOCKETS=true
```

## Шаг 4: Запуск

Просто запустите ваш Python-файл:
```bash
python my_worker.py
```
Воркер автоматически подключится к Оркестратору, зарегистрируется и начнет опрашивать его на наличие новых задач.

## Механизмы отмены задач

SDK предоставляет два механизма отмены:

1.  **WebSocket (Push-модель):** Если `WORKER_ENABLE_WEBSOCKETS=true`, Оркестратор может отправить команду на немедленную отмену. Это вызывает исключение `asyncio.CancelledError` в вашем обработчике. Этот способ обеспечивает самую быструю реакцию, и вам нужно лишь обернуть ваш код в `try...except asyncio.CancelledError` для корректной очистки ресурсов, если это необходимо.

2.  **Redis (Pull-модель):** Даже без WebSocket, вы можете реализовать "кооперативную" отмену для очень долгих задач. Для этого SDK предоставляет асинхронную функцию `worker.check_for_cancellation(task_id)`. Вы должны периодически вызывать ее внутри вашего цикла обработки. Если функция вернет `True`, это значит, что Оркестратор запросил отмену. Ваш код должен корректно прервать выполнение и вернуть статус `cancelled`. (См. пример `long_running_task` выше).
