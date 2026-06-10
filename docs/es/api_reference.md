[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/api_reference.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/api_reference.md) | **ES**

# Referencia de la API del Orquestador

Este documento describe todos los puntos finales HTTP proporcionados por el servidor del Orquestador. La API se divide en tres grupos lógicos: Pública, Cliente e Interna (para Workers).

## Autenticación

-   **Cliente -> Orquestador:** Todas las solicitudes a los puntos finales de cliente (prefijo predeterminado `/api/v1/*`) deben contener el encabezado `X-Client-Token`. La parte `/api` es configurable mediante `CLIENT_API_PREFIX`.
-   **Worker -> Orquestador:** Todas las solicitudes a los puntos finales `/_worker/*` deben contener el encabezado `X-Worker-Token` con un token de worker válido (individual o global).

---

## 1. Puntos Finales Públicos (`/_public`)

Estos puntos finales no requieren autenticación y siempre utilizan el prefijo fijo `/_public`.

### Verificación del Estado del Servicio
-   **Punto final:** `GET /_public/status`
-   **Descripción:** Confirma que el servicio se está ejecutando.
-   **Respuesta (`200 OK`):** `{"status": "ok"}`

### Webhook para "Aprobación Humana"
-   **Punto final:** `POST /_public/webhooks/approval/{job_id}`
-   **Descripción:** Permite la aprobación manual de un paso del pipeline.
-   **Respuesta (`200 OK`):** `{"status": "approval_received"}`

---

## 2. Puntos Finales de Cliente (`/{CLIENT_API_PREFIX}/v1`)

Requieren encabezado `X-Client-Token`.
La ruta base para estos puntos finales es configurable a través de la variable de entorno `CLIENT_API_PREFIX` (por defecto `api`). Si se establece como una cadena vacía, estos puntos finales estarán disponibles en la raíz (ej. `/v1/...`).

### Crear Nuevo Trabajo
-   **Punto final:** `POST /{CLIENT_API_PREFIX}/v1/{blueprint_api_endpoint}`
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
-   **Descripción:** Devuelve el estado actual del trabajo. El nivel de detalle depende de la configuración `DETAILED_API_RESPONSES` del servidor.

#### Estados del Trabajo (Job Statuses)
El sistema utiliza los siguientes estados para gestionar el ciclo de vida del trabajo:

**Estados Intermedios:**
*   `pending`: Trabajo creado y esperando a ser encolado.
*   `running`: El trabajo se está procesando activamente.
*   `waiting_for_worker`: Esperando a que un worker adecuado recoja la tarea.
*   `waiting_for_human`: Esperando aprobación o acción manual (Human-in-the-loop).
*   `waiting_for_parallel`: Esperando a que se completen todas las subtareas en una rama paralela.

**Estados Terminales:**
*   `finished`: Trabajo completado con éxito. El resultado final está disponible.
*   `failed`: Error de lógica o del worker. Los detalles del error están disponibles.
*   `cancelled`: El trabajo fue cancelado por un usuario o por el sistema.
*   `error`: Error crítico del sistema durante la ejecución.
*   `quarantined`: Trabajo puesto en cuarentena para revisión manual (por ejemplo, debido a una violación del contrato).

-   **Parámetros:** `?fields=status,result` — filtrado de campos devueltos.

**Nota de seguridad:** El campo `result` solo se completa y es visible cuando el trabajo alcanza un estado terminal (`finished` o `failed`). Para trabajos en estados intermedios (`running`, `waiting`), el resultado se oculta para proteger los datos confidenciales en curso.

**Aislamiento de datos:** El acceso está estrictamente limitado al cliente que creó el trabajo. Debe proporcionar el mismo `X-Client-Token` que se utilizó para iniciar el trabajo. Cualquier intento de acceder a un ID de trabajo perteneciente a otro cliente devolverá un `404 Not Found` por razones de seguridad (para evitar la enumeración de ID de trabajo).

**Respuesta Compacta (Por defecto):**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "finished",
  "result": { "output": "success" },
  "blueprint_name": "my_flow"
}
```

### Operaciones con S3
-   `GET /api/v1/jobs/{job_id}/files/upload` — obtener URL de subida.
-   `PUT /api/v1/jobs/{job_id}/files/content/{filename}` — streaming directo de archivos.
-   `GET /api/v1/jobs/{job_id}/files/download/{filename}` — enlace estable de descarga.

### Gestión y Diagnóstico
-   `POST /api/v1/jobs/{job_id}/cancel` — cancela una tarea activa.
-   `GET /api/v1/jobs/{job_id}/history` — historial completo de eventos del trabajo. En modo compacto, los campos `context_snapshot` en los eventos se filtran.
-   `GET /api/v1/jobs` — lista de todos los trabajos. En modo compacto, el `context_snapshot` de cada trabajo se filtra.
-   `GET /api/v1/blueprints/{blueprint_name}/graph` — estructura del blueprint en formato DOT.
-   `GET /api/v1/workers` — lista de todos los workers activos.
-   `GET /api/v1/dashboard` — estadísticas agregadas del sistema.
-   `GET /api/v1/workers/catalog` — catálogo de habilidades (caché 10s).


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
