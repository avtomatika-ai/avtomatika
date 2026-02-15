[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/api_reference.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/api_reference.md)

# Referencia de la API del Orquestador

Este documento describe todos los puntos finales HTTP proporcionados por el servidor del Orquestador. La API se divide en tres grupos lógicos: Pública, Cliente e Interna (para Workers).

## Autenticación

-   **Cliente -> Orquestador:** Todas las solicitudes a los puntos finales `/api/*` deben contener el encabezado `X-Client-Token` con el token del cliente.
-   **Worker -> Orquestador:** Todas las solicitudes a los puntos finales `/_worker/*` deben contener el encabezado `X-Worker-Token` con un token de worker válido (individual o global).

---

## 1. Puntos Finales Públicos (`/_public`)

Estos puntos finales no requieren autenticación.

### Verificación del Estado del Servicio

-   **Punto final:** `GET /_public/status`
-   **Descripción:** Devuelve una respuesta simple confirmando que el servicio se está ejecutando.
-   **Respuesta (`200 OK`):** `{"status": "ok"}`

### Métricas de Prometheus

-   **Punto final:** `GET /_public/metrics`
-   **Descripción:** Devuelve métricas de la aplicación en un formato compatible con Prometheus.
-   **Respuesta (`200 OK`):** Respuesta de texto con métricas.

### Webhook para "Aprobación Humana"

-   **Punto final:** `POST /_public/webhooks/approval/{job_id}`
-   **Descripción:** Permite a un sistema externo aprobar o rechazar un paso en el pipeline.
-   **Cuerpo de la Solicitud:** `{"decision": "approved"}`
-   **Respuesta (`200 OK`):** `{"status": "approval_received", "job_id": "..."}`

### Punto Final de Depuración para Limpieza de BD

-   **Punto final:** `POST /_public/debug/flush_db`
-   **Descripción:** **(¡Solo desarrollo!)** Borra toda la base de datos Redis.
-   **Respuesta (`200 OK`):** `{"status": "db_flushed"}`

### Documentación Interactiva de la API

-   **Punto final:** `GET /_public/docs`
-   **Descripción:** Devuelve una página HTML interactiva con documentación de la API (estilo Swagger UI).
-   **Respuesta (`200 OK`):** Página HTML.

---

## 2. Puntos Finales de Cliente (`/api`)

Estos puntos finales están diseñados para sistemas externos que inician y monitorean flujos de trabajo. Requiere encabezado `X-Client-Token`.

### Crear Nuevo Trabajo

-   **Punto final:** `POST /api/{api_version}/{blueprint_api_endpoint}`
-   **Ejemplo:** `POST /api/v1/jobs/simple_flow`
-   **Descripción:** Crea e inicia una nueva instancia (Job) del blueprint especificado.
-   **Cuerpo de la Solicitud:**
    ```json
    {
      "initial_data": { ... },
      "webhook_url": "https://callback.url/webhook",
      "dispatch_timeout": 60,
      "result_timeout": 300
    }
    ```
    *   `initial_data` (objeto, opcional): Datos iniciales para el trabajo.
    *   `webhook_url` (cadena, opcional): URL para recibir notificaciones asíncronas sobre la finalización, fallo o cuarentena del trabajo.
    *   `dispatch_timeout` (entero, opcional): Tiempo máximo en segundos que una tarea puede esperar en la cola por un worker.
    *   `result_timeout` (entero, opcional): Plazo absoluto en segundos para recibir el resultado desde la creación del trabajo.
-   **Respuesta (`202 Accepted`):** `{"status": "accepted", "job_id": "..."}`
-   **Respuesta (`429 Too Many Requests`):** Si el cliente o IP excede el límite de velocidad configurado.

### Obtener Estado del Trabajo

-   **Punto final:** `GET /api/v1/jobs/{job_id}`
-   **Descripción:** Devuelve el estado actual completo del trabajo especificado.
-   **Respuesta (`200 OK`):** Objeto JSON con el estado del `Job`.
-   **Respuesta (`404 Not Found`):** Si no se encuentra un trabajo con tal ID.

### Obtener URL de Subida a S3

-   **Punto final:** `GET /api/v1/jobs/{job_id}/files/upload`
-   **Descripción:** Genera una URL presignada de S3 temporal para subir un archivo directamente al almacenamiento del trabajo.
-   **Parámetros de consulta:**
    *   `filename` (cadena, requerido): Nombre del archivo a subir.
    *   `expires_in` (entero, opcional): Validez del enlace en segundos. Por defecto: `3600`.
-   **Respuesta (`200 OK`):** `{"url": "...", "expires_in": 3600, "method": "PUT"}`
-   **Respuesta (`501 Not Implemented`):** Si el soporte de S3 no está habilitado.

### Subir Archivo (Streaming Directo)

-   **Punto final:** `PUT /api/v1/jobs/{job_id}/files/content/{filename}`
-   **Descripción:** Sube un archivo directamente a S3 a través del proxy de streaming del Orquestador. Evita el disco local y utiliza una RAM mínima.
-   **Cuerpo:** Contenido binario del archivo.
-   **Respuesta (`200 OK`):** `{"status": "uploaded", "s3_uri": "..."}`

### Descargar Archivo (Enlace Estable)

-   **Punto final:** `GET /api/v1/jobs/{job_id}/files/download/{filename}`
-   **Descripción:** Un enlace estable que redirige automáticamente a una URL presignada de S3 fresca. Útil para descargas de navegador.
-   **Respuesta (`302 Found`):** Redirige a la URL de S3.

### Cancelar Tarea en Ejecución

- **Punto final**: `POST /api/v1/jobs/{job_id}/cancel`
- **Descripción**: Inicia la cancelación de una tarea que está siendo ejecutada por un worker.
- **Respuesta (`200 OK`):** `{"status": "cancellation_request_sent"}` (si es vía WebSocket) o `{"status": "cancellation_request_accepted"}` (si es vía bandera Redis).

### Obtener Historial del Trabajo

-   **Punto final:** `GET /api/v1/jobs/{job_id}/history`
-   **Descripción:** Devuelve el historial completo de eventos para el trabajo especificado (si el almacenamiento de historial está habilitado).
-   **Respuesta (`200 OK`):** Matriz de objetos de evento.

### Obtener Grafo del Blueprint

-   **Punto final:** `GET /api/v1/blueprints/{blueprint_name}/graph`
-   **Descripción:** Devuelve la estructura del blueprint en formato DOT para visualización.
-   **Respuesta (`200 OK`):** Respuesta de texto en formato `text/vnd.graphviz`.

### Obtener Lista de Workers Activos

-   **Punto final:** `GET /api/v1/workers`
-   **Descripción:** Devuelve una lista de todos los workers actualmente activos.
-   **Respuesta (`200 OK`):** Matriz de objetos con información del worker.

### Obtener Datos del Tablero

-   **Punto final:** `GET /api/v1/dashboard`
-   **Descripción:** Devuelve estadísticas agregadas sobre el estado del sistema.
-   **Respuesta (`200 OK`):** Objeto JSON con estadísticas.

---

## 3. Puntos Finales Internos para Workers (`/_worker`)

Estos puntos finales son utilizados por los workers para registrarse, recibir tareas y enviar resultados. Requiere encabezado `X-Worker-Token`.

### Registrar Worker

-   **Punto final:** `POST /_worker/workers/register`
-   **Descripción:** Registra un worker en el sistema.
-   **Cuerpo de la Solicitud:** Objeto JSON con descripción completa del worker (ID, tareas soportadas, recursos, etc.).
    ```json
    {
      "worker_id": "worker-123",
      "supported_skills": ["video_processing", "audio_transcription"]
    }
    ```
-   **Respuesta (`200 OK`):** `{"status": "registered"}`

### Latido / Actualización de Estado

-   **Punto final:** `PATCH /_worker/workers/{worker_id}`
-   **Descripción:** Punto final universal para confirmar actividad y actualizar estado.
    -   **Cuerpo Vacío:** Actúa como un "ping" ligero, solo actualiza el TTL del worker.
    -   **Cuerpo de la Solicitud (JSON):** Actualiza datos del worker (estado, carga, tareas disponibles) y actualiza el TTL.
-   **Respuesta (`200 OK`):** `{"status": "ttl_refreshed"}` o JSON con estado actualizado del worker.

### Obtener Siguiente Tarea (Sondeo Largo)

-   **Punto final:** `GET /_worker/workers/{worker_id}/tasks/next`
-   **Descripción:** El worker solicita la siguiente tarea. La conexión se mantiene abierta si no hay tareas disponibles.
-   **Respuesta (`200 OK`):** Objeto JSON con datos de la tarea.
-   **Respuesta (`204 No Content`):** Devuelto al expirar el tiempo de espera si no aparecieron nuevas tareas.

### Enviar Resultado de Tarea

-   **Punto final:** `POST /_worker/tasks/result`
-   **Descripción:** El worker envía el resultado de una tarea completada.
-   **Cuerpo de la Solicitud:**
    ```json
    {
      "job_id": "...",
      "task_id": "...",
      "worker_id": "...",
      "status": "success",
      "data": { "output": "..." },
      "error": null
    }
    ```
-   **Respuesta (`200 OK`):** `{"status": "result_accepted_success"}`

### Establecer Conexión WebSocket

- **Punto final**: `GET /_worker/ws/{worker_id}`
- **Descripción**: Establece una conexión WebSocket para recibir comandos en tiempo real del orquestador (por ejemplo, `cancel_task`).
- **Protocolo**: `WebSocket`
