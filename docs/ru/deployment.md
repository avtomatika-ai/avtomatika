[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/deployment.md) | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/deployment.md) | **RU**

# Развертывание и Тестирование

Этот документ содержит рекомендации по запуску системы в продакшене, а также инструкции для локальной разработки и тестирования.

## Промышленное развертывание

Встроенный сервер `aiohttp` отлично подходит для разработки, но для продакшена рекомендуется использовать специализированные ASGI-серверы.

### Вариант 1: Gunicorn (Рекомендуемый)

```bash
# Вам понадобится файл app.py, который инициализирует движок и возвращает объект app
gunicorn app:app --bind 0.0.0.0:8080 --worker-class aiohttp.GunicornWebWorker
```

### Вариант 2: Uvicorn

```bash
# Установка: pip install uvicorn
uvicorn app:app --host 0.0.0.0 --port 8080
```

## Локальная разработка

### Запуск всей системы (Docker Compose)

Самый простой способ запустить все компоненты (Оркестратор, Redis, Воркер):

```bash
docker-compose up --build
```

-   **Orchestrator API** будет доступен по адресу `http://localhost:8080`.

### Запуск тестов

Крайне **важно** запускать тесты после внесения любых изменений.

**1. Настройка окружения:**
```bash
pip install -e ".[all,test]"
```

**2. Запуск тестов:**
```bash
pytest tests/
```

## Дополнительная документация

- [**Гайд по архитектуре**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/architecture.md)
- [**Справочник API**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/api_reference.md)
- [**Гайд по конфигурации**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/configuration.md)
