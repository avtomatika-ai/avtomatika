[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/api_reference.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/api_reference.md) | **ES**

# Referencia de la API del Orquestador

Este documento describe todos los puntos finales HTTP proporcionados por el servidor del Orquestador. La API se divide en tres grupos lógicos: Pública, Cliente e Interna (para Workers).

## Autenticación

-   **Cliente -> Orquestador:** Todas las solicitudes a los puntos finales `/api/v1/*` deben contener el encabezado `X-Client-Token` con el token del cliente.
-   **Worker -> Orquestador:** Todas las solicitudes a los puntos finales `/_worker/*` deben contener el encabezado `X-Worker-Token` con un token de worker válido.

---

## 1. Puntos Finales Públicos (`/_public`)

Estos puntos finales no requieren autenticación.

### Verificación del Estado del Servicio
-   **Punto final:** `GET /_public/status`
-   **Descripción:** Confirma que el servicio se está ejecutando.
-   **Respuesta (`200 OK`):** `{"status": "ok"}`

### Métricas de Prometheus
-   **Punto final:** `GET /_public/metrics`
-   **Descripción:** Métricas de la aplicación, incluyendo la medición del lag del Event Loop.
-   **Respuesta (`200 OK`):** Respuesta de texto con métricas.

### Webhook para "Aprobación Humana"
-   **Punto final:** `POST /_public/webhooks/approval/{job_id}`
-   **Descripción:** Permite la aprobación manual de un paso del pipeline.
-   **Respuesta (`200 OK`):** `{"status": "approval_received"}`

---

## 2. Puntos Finales de Cliente (`/api/v1`)

Requieren encabezado `X-Client-Token`.

### Crear Nuevo Trabajo
-   **Punto final:** `POST /api/v1/{blueprint_api_endpoint}`
-   **Descripción:** Inicia una nueva instancia (Job) de un blueprint.
-   **Cuerpo de la Solicitud:**
    ```json
    {
      "initial_data": { ... },
      "webhook_url": "https://callback.url/webhook",
      "dispatch_timeout": 60,
      "result_timeout": 300
    }
    ```
-   **Respuesta (`202 Accepted`):** `{"status": "accepted", "job_id": "..."}`

### Obtener Estado del Trabajo
-   **Punto final:** `GET /api/v1/jobs/{job_id}`
-   **Descripción:** Devuelve el estado actual del trabajo.
-   **Parámetros:** `?fields=status,result` — filtrado de campos devueltos.
-   **Respuesta (`200 OK`):** JSON del trabajo.

### Operaciones con S3
-   `GET /api/v1/jobs/{job_id}/files/upload` — obtener URL de subida.
-   `PUT /api/v1/jobs/{job_id}/files/content/{filename}` — streaming directo de archivos.
-   `GET /api/v1/jobs/{job_id}/files/download/{filename}` — enlace estable de descarga.

### Gestión y Diagnóstico
-   `POST /api/v1/jobs/{job_id}/cancel` — cancelar tarea activa.
-   `GET /api/v1/jobs/{job_id}/history` — historial completo de eventos del trabajo.
-   `GET /api/v1/blueprints/{blueprint_name}/graph` — estructura del blueprint en formato DOT.
-   `GET /api/v1/workers` — lista de workers activos.
-   `GET /api/v1/dashboard` — estadísticas agregadas del sistema.
-   `GET /api/v1/workers/catalog` — catálogo de habilidades (caché 10 seg).

---

## 3. Puntos Finales Internos para Workers (`/_worker`)

Requieren encabezado `X-Worker-Token`.

### Registro y Latido (Heartbeat)
-   **POST /_worker/workers/register** — registro del worker y sus capacidades.
-   **PATCH /_worker/workers/{worker_id}** — confirmación de actividad. Devuelve `next_heartbeat_jitter_ms`.

### Tareas y Resultados
-   **GET /_worker/workers/{worker_id}/tasks/next** — solicitud de nueva tarea (Long-Polling + Work Stealing).
-   **POST /_worker/tasks/result** — envío del resultado de la tarea.
-   **POST /_worker/events** — envío de evento intermedio (progreso, logs).

### WebSocket
-   **GET /_worker/ws/{worker_id}** — canal para comandos en tiempo real (ej. `cancel_task`).
