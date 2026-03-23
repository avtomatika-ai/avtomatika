[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/api_reference.md) | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/api_reference.md) | **RU**

# Справочник по API Оркестратора

Этот документ описывает все HTTP-эндпоинты, предоставляемые сервером Оркестратора. API разделен на три логические группы: Публичные, Клиентские и Внутренние (для Воркеров).

## Аутентификация

-   **Клиент -> Оркестратор:** Все запросы к эндпоинтам `/api/v1/*` должны содержать заголовок `X-Client-Token` с токеном клиента.
-   **Воркер -> Оркестратор:** Все запросы к эндпоинтам `/_worker/*` должны содержать заголовок `X-Worker-Token` с валидным токеном воркера.

---

## 1. Публичные эндпоинты (`/_public`)

Эти эндпоинты не требуют аутентификации.

### Проверка статуса сервиса
-   **Эндпоинт:** `GET /_public/status`
-   **Описание:** Подтверждает работоспособность сервиса.
-   **Ответ (`200 OK`):** `{"status": "ok"}`

### Метрики Prometheus
-   **Эндпоинт:** `GET /_public/metrics`
-   **Описание:** Метрики приложения, включая замер лага Event Loop.
-   **Ответ (`200 OK`):** Текст с метриками.

### Вебхук "Aprobación Humana"
-   **Эндпоинт:** `POST /_public/webhooks/approval/{job_id}`
-   **Описание:** Ручное подтверждение шага пайплайна.
-   **Ответ (`200 OK`):** `{"status": "approval_received"}`

---

## 2. Клиентские эндпоинты (`/api/v1`)

Требуют заголовок `X-Client-Token`.

### Создание нового задания
-   **Эндпоинт:** `POST /api/v1/{blueprint_api_endpoint}`
-   **Описание:** Запуск нового экземпляра (Job) блупринта.
-   **Тело запроса:**
    ```json
    {
      "initial_data": { ... },
      "webhook_url": "https://callback.url/webhook",
      "dispatch_timeout": 60,
      "result_timeout": 300
    }
    ```
-   **Ответ (`202 Accepted`):** `{"status": "accepted", "job_id": "..."}`

### Получение статуса задания
-   **Эндпоинт:** `GET /api/v1/jobs/{job_id}`
-   **Описание:** Возвращает текущее состояние задания.
-   **Параметры:** `?fields=status,result` — фильтрация возвращаемых полей.
-   **Ответ (`200 OK`):** JSON задания.

### Работа с S3
-   `GET /api/v1/jobs/{job_id}/files/upload` — получение ссылки для загрузки.
-   `PUT /api/v1/jobs/{job_id}/files/content/{filename}` — прямой стриминг файла.
-   `GET /api/v1/jobs/{job_id}/files/download/{filename}` — стабильная ссылка на скачивание.

### Управление и диагностика
-   `POST /api/v1/jobs/{job_id}/cancel` — отмена активной задачи.
-   `GET /api/v1/jobs/{job_id}/history` — полная история событий задания.
-   `GET /api/v1/blueprints/{blueprint_name}/graph` — структура блупринта в формате DOT.
-   `GET /api/v1/workers` — список всех активных воркеров.
-   `GET /api/v1/dashboard` — агрегированная статистика системы.
-   `GET /api/v1/workers/catalog` — каталог навыков (кэш 10 сек).

---

## 3. Внутренние эндпоинты для Воркеров (`/_worker`)

Требуют заголовок `X-Worker-Token`.

### Регистрация и Heartbeat
-   **POST /_worker/workers/register** — регистрация воркера и его возможностей.
-   **PATCH /_worker/workers/{worker_id}** — подтверждение активности (Heartbeat). Возвращает `next_heartbeat_jitter_ms`.

### Задачи и результаты
-   **GET /_worker/workers/{worker_id}/tasks/next** — получение новой задачи (Long-Polling + Work Stealing).
-   **POST /_worker/tasks/result** — отправка результата выполнения задачи.
-   **POST /_worker/events** — отправка промежуточного события (прогресс, логи).

### WebSocket
-   **GET /_worker/ws/{worker_id}** — канал для команд реального времени (например, `cancel_task`).
