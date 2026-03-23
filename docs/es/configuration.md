**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/configuration.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/configuration.md)

# Referencia de Archivos de Configuración

Avtomatika utiliza archivos en formato [TOML](https://toml.io/en/) para gestionar el acceso y la configuración de clientes y workers.

El sistema implementa el principio **Fail Fast**: si se detecta un error de sintaxis TOML o faltan campos obligatorios al inicio, el Orquestador **no se iniciará** y lanzará una excepción. Esto asegura que el sistema no se ejecute en un estado incorrecto o inseguro.

---

## 1. clients.toml

Este archivo define la lista de clientes API autorizados para crear trabajos.

**Ruta Predeterminada:** Se puede establecer a través de la variable de entorno `CLIENTS_CONFIG_PATH`. Generalmente se encuentra en la raíz del proyecto.

### Estructura

El archivo consta de secciones (tablas), donde el nombre de la sección es el identificador único del cliente.

| Campo | Tipo | Obligatorio | Descripción |
| :--- | :--- | :--- | :--- |
| `token` | String | **Sí** | Token secreto que el cliente debe pasar en el encabezado `X-Client-Token`. |
| `plan` | String | No | Nombre del plan de tarifa (ej. "free", "premium"). Usado en blueprints. |
| `monthly_attempts` | Entero | No | Cuota mensual de solicitudes. |
| `*` | Cualquiera | No | Cualquier otro campo estará disponible en `context.client.params`. |

### Ejemplo

```toml
# Cliente Premium
[client_premium_user]
token = "sec_vip_token_123"
plan = "premium"
monthly_attempts = 100000
# Parámetros personalizados
languages = ["en", "es", "fr"]
priority_support = true
```

---

## 2. workers.toml

Este archivo se utiliza para configurar la autenticación individual de los workers. Es una alternativa más segura al uso de un único token global.

**Ruta Predeterminada:** Se puede establecer a través de la variable de entorno `WORKERS_CONFIG_PATH`.

### Estructura

El archivo consta de secciones, donde el nombre de la sección debe **coincidir exactamente** con el `worker_id` que utiliza el worker al registrarse.

| Campo | Tipo | Obligatorio | Descripción |
| :--- | :--- | :--- | :--- |
| `token` | String | **Sí** | Token secreto individual para este worker. |
| `description` | String | No | Descripción del worker para los administradores. |

### Características de Seguridad
*   Al inicio, el Orquestador calcula el hash SHA-256 del token y solo almacena el hash en memoria (Redis). El token original no se guarda.
*   **Optimización de Rendimiento:** Los hashes de los tokens se cachean en la memoria del Orquestador para minimizar el uso de CPU durante los latidos frecuentes.

### Ejemplo

```toml
[gpu-worker-01]
token = "super-secret-token-for-gpu-01"
description = "Nodo GPU primario para renderizado de video"

[cpu-worker-01]
token = "another-secret-token-for-cpu-01"
description = "Worker de CPU de propósito general"
```

---

## 3. schedules.toml

Este archivo configura el Programador Nativo, permitiéndote ejecutar blueprints periódicamente sin necesidad de cron jobs externos.

**Ruta Predeterminada:** Se puede establecer a través de la variable de entorno `SCHEDULES_CONFIG_PATH`.

### Estructura

Cada sección representa un trabajo programado. El nombre de la sección sirve como identificador único.

| Campo | Tipo | Obligatorio | Descripción |
| :--- | :--- | :--- | :--- |
| `blueprint` | String | **Sí** | El nombre del blueprint a ejecutar. |
| `input_data` | Dictionary | No | Datos iniciales para el trabajo. |
| `dispatch_timeout` | Entero | No | Expiración de la cola (segundos). |
| `result_timeout` | Entero | No | Límite de tiempo absoluto para el resultado (segundos). |
| `interval_seconds` | Entero | *Uno de* | Ejecutar cada N segundos. |
| `daily_at` | String | *Uno de* | Ejecutar diariamente a una hora específica ("HH:MM"). |
| `weekly_days` | List[String] | *Uno de* | Ejecutar en días específicos ("mon", "tue", ...) a la hora `time`. |
| `monthly_dates` | List[Integer] | *Uno de* | Ejecutar en fechas específicas (1-31) a la hora `time`. |
| `time` | String | *Uno de* | Requerido para `weekly_days` y `monthly_dates`. Formato "HH:MM". |

**Nota sobre Zonas Horarias:** Todos los campos de tiempo se interpretan de acuerdo con la variable de entorno global `TZ` (por defecto "UTC").

### Ejemplo

```toml
[cleanup_job]
blueprint = "system_cleanup"
interval_seconds = 3600
input_data = { target = "temp_files" }

[daily_report]
blueprint = "generate_report"
daily_at = "09:00"
input_data = { type = "full_daily" }
```

---

## Recarga Dinámica

Puedes actualizar `workers.toml` sin reiniciar el Orquestador.
Para hacer esto, envía una solicitud POST (con un token de cliente válido) al endpoint:

`POST /api/v1/admin/reload-workers`

Esto obliga al Orquestador a volver a leer el archivo y actualizar los hashes de los tokens en Redis.

---

## Variables de Entorno

| Variable | Descripción | Predeterminado |
| :--- | :--- | :--- |
| `API_HOST` | Host para el servidor API. | `0.0.0.0` |
| `API_PORT` | Puerto para el servidor API. | `8080` |
| `ENABLE_CLIENT_API` | **Pure Holon Mode:** Si es `false`, deshabilita la API de Cliente (`/api/v1/...`). | `true` |
| `REDIS_HOST` | Hostname del servidor Redis. Requerido para producción. | `""` |
| `REDIS_PORT` | Puerto de Redis. | `6379` |
| `REDIS_DB` | Índice de base de datos Redis. | `0` |
| `INSTANCE_ID` | Identificador único para esta instancia del Orquestador. | `hostname` |
| `CLIENT_TOKEN` | Token global para clientes API (respaldo). | `secure-orchestrator-token` |
| `GLOBAL_WORKER_TOKEN` | Token global para workers (respaldo). | `secure-worker-token` |
| `WORKERS_CONFIG_PATH` | Ruta a `workers.toml`. | `""` |
| `CLIENTS_CONFIG_PATH` | Ruta a `clients.toml`. | `""` |
| `SCHEDULES_CONFIG_PATH` | Ruta a `schedules.toml`. | `""` |
| `BLUEPRINTS_DIR` | Directorio con archivos Python de Blueprints. | `""` |
| `TZ` | **Zona Horaria Global:** Afecta a disparadores y marcas de tiempo (ej. "Europe/Madrid"). | `UTC` |
| `LOG_LEVEL` | Nivel de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`). | `INFO` |
| `LOG_FORMAT` | Formato de log (`text` o `json`). | `json` |
| `WORKER_TIMEOUT_SECONDS` | Tiempo límite por defecto para que un worker complete una tarea. | `300` |
| `WORKER_POLL_TIMEOUT_SECONDS` | Tiempo límite para peticiones de sondeo (polling) de tareas. | `30` |
| `WORKER_HEALTH_CHECK_INTERVAL_SECONDS` | Intervalo para actualizar el TTL del worker. | `60` |
| `JOB_MAX_RETRIES` | Máximo de reintentos para fallos transitorios. | `3` |
| `WATCHER_INTERVAL_SECONDS` | Intervalo del proceso Watcher para verificar expiraciones. | `20` |
| `WATCHER_LIMIT` | Número máximo de trabajos expirados procesados por ciclo de Watcher. | `500` |
| `STRICT_EVENT_VALIDATION` | Si es `true`, rechaza eventos de workers no declarados en sus esquemas. | `true` |
| `REPUTATION_MIN_THRESHOLD` | Puntuación mínima de reputación para asignar tareas a un worker. | `0.3` |
| `REPUTATION_PENALTY_CONTRACT_VIOLATION` | Penalización de reputación por violación de contrato. | `0.2` |
| `REPUTATION_PENALTY_TASK_FAILURE` | Penalización por fallo crítico de tarea. | `0.05` |
| `REPUTATION_REWARD_SUCCESS` | Recompensa de reputación por tarea exitosa. | `0.001` |
| `EXECUTOR_MAX_CONCURRENT_JOBS` | Número máximo de trabajos concurrentes procesados por el Orquestador. | `1000` |
| `DISPATCHER_SOFT_LIMIT` | Límite suave de tareas en la cola de un worker antes del desbordamiento. | `3` |
| `DISPATCHER_MAX_CANDIDATES` | Número máximo de workers adecuados verificados por el Dispatcher. | `50` |
| `WORK_STEALING_ENABLED` | Si es `true`, los workers inactivos pueden robar tareas de colegas cargados. | `true` |
| `HISTORY_DATABASE_URI` | URI para el almacenamiento de historial (`sqlite:///...` o `postgresql://...`). | `""` |
| `RATE_LIMITING_ENABLED` | Habilitar middleware de límite de tasa. | `true` |
| `MAX_TRANSITIONS_PER_JOB` | **Protección de Bucle Infinito:** Límite de transiciones por trabajo. | `100` |

### Seguridad y TLS (mTLS)

| Variable | Descripción | Predeterminado |
| :--- | :--- | :--- |
| `TLS_ENABLED` | Habilitar servidor HTTPS. | `false` |
| `TLS_CERT_PATH` | Ruta al certificado SSL del servidor (`.crt`). | `""` |
| `TLS_KEY_PATH` | Ruta a la clave privada del servidor (`.key`). | `""` |
| `TLS_CA_PATH` | Ruta al paquete CA para verificar certificados de cliente. | `""` |
| `TLS_REQUIRE_CLIENT_CERT` | Si es `true`, el servidor rechazará conexiones sin certificado válido (mTLS). | `false` |

### Almacenamiento S3 (Opcional)

| Variable | Descripción | Predeterminado |
| :--- | :--- | :--- |
| `S3_ENDPOINT_URL` | URL del almacenamiento compatible con S3. | `""` |
| `S3_ACCESS_KEY` | ID de clave de acceso. | `""` |
| `S3_SECRET_KEY` | Clave de acceso secreta. | `""` |
| `S3_DEFAULT_BUCKET` | Nombre del bucket por defecto. | `avtomatika-payloads` |
| `S3_REGION` | Región S3. | `us-east-1` |
| `S3_MAX_CONCURRENCY` | Máximo de conexiones concurrentes a S3 para todo el clúster. | `100` |
| `S3_AUTO_CLEANUP` | Si es `true`, borra automáticamente archivos S3 al finalizar el trabajo. | `true` |
| `TASK_FILES_DIR` | Directorio local para almacenamiento temporal de archivos. | `/tmp/avtomatika-payloads` |
