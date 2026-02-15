[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/configuration.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/configuration.md)

# Referencia de Archivos de Configuración

Avtomatika utiliza archivos en formato [TOML](https://toml.io/en/) para gestionar el acceso y la configuración de clientes y workers.

El sistema implementa el principio **Fail Fast**: si se detecta un error de sintaxis TOML o faltan campos obligatorios al inicio, el Orquestador **no se iniciará** y lanzará una excepción. Esto asegura que el sistema no se ejecute en un estado incorrecto o inseguro.

---

## 1. clients.toml

Este archivo define la lista de clientes API autorizados para crear trabajos.

**Ruta Predeterminada:** Se puede establecer a través de la variable de entorno `CLIENTS_CONFIG_PATH`. Generalmente se encuentra en la raíz del proyecto.

### Estructura

El archivo consta de secciones (tablas), donde el nombre de la sección es el identificador único del cliente (para registros y conveniencia).

| Campo | Tipo | Obligatorio | Descripción |
| :--- | :--- | :--- | :--- |
| `token` | Cadena | **Sí** | Token secreto que el cliente debe pasar en el encabezado `X-Client-Token`. |
| `plan` | Cadena | No | Nombre del plan tarifario (por ejemplo, "free", "premium"). Utilizado en blueprints para la lógica. |
| `monthly_attempts` | Entero | No | Cuota mensual de solicitudes. Si se establece, el Orquestador rastreará y bloqueará las solicitudes que excedan el límite. |
| `*` | Cualquiera | No | Cualquier otro campo (por ejemplo, `languages`, `callback_url`) estará disponible en `context.client.params`. |

### Ejemplo

```toml
# Cliente premium
[client_premium_user]
token = "sec_vip_token_123"
plan = "premium"
monthly_attempts = 100000
# Parámetros personalizados
languages = ["en", "de", "fr"]
priority_support = true

# Cliente gratuito
[client_free_user]
token = "sec_free_token_456"
plan = "free"
monthly_attempts = 100
languages = ["en"]
```

---

## 2. workers.toml

Este archivo se utiliza para configurar la autenticación individual de workers. Esta es una alternativa más segura al uso de un solo token global.

**Ruta Predeterminada:** Se puede establecer a través de la variable de entorno `WORKERS_CONFIG_PATH`.

### Estructura

El archivo consta de secciones, donde el nombre de la sección debe **coincidir exactamente** con el `worker_id` que el worker utiliza al registrarse.

| Campo | Tipo | Obligatorio | Descripción |
| :--- | :--- | :--- | :--- |
| `token` | Cadena | **Sí** | Token secreto individual para este worker. |
| `description` | Cadena | No | Descripción del worker para administradores. |

### Características de Seguridad
*   Al inicio, el Orquestador calcula el hash SHA-256 del token y almacena solo el hash en memoria (Redis). El token original no se almacena en ninguna parte.
*   Al recibir una solicitud entrante de un worker, el hash del token proporcionado se compara con el almacenado.

### Ejemplo

```toml
# El nombre de la sección debe ser el mismo que WORKER_ID en el lado del worker
[gpu-worker-01]
token = "super-secret-token-for-gpu-01"
description = "Nodo GPU principal para renderizado de video"

[cpu-worker-01]
token = "another-secret-token-for-cpu-01"
description = "Worker de CPU de propósito general"
```

---

## 3. schedules.toml

Este archivo configura el Programador Nativo (Native Scheduler), permitiéndote ejecutar blueprints periódicamente sin trabajos cron externos.

**Ruta Predeterminada:** Se puede establecer a través de la variable de entorno `SCHEDULES_CONFIG_PATH`.

### Estructura

Cada sección representa un trabajo programado. El nombre de la sección sirve como identificador único del trabajo.

| Campo | Tipo | Obligatorio | Descripción |
| :--- | :--- | :--- | :--- |
| `blueprint` | Cadena | **Sí** | El nombre del blueprint a ejecutar. |
| `input_data` | Diccionario | No | Carga útil de datos iniciales para el trabajo. Por defecto es un diccionario vacío. |
| `dispatch_timeout` | Entero | No | Expiración de cola (segundos). El trabajo falla si no es recogido por un worker. |
| `result_timeout` | Entero | No | Plazo de ejecución (segundos). Tiempo absoluto desde la creación para el resultado. |
| `interval_seconds` | Entero | *Uno de* | Ejecutar trabajo cada N segundos. |
| `daily_at` | Cadena | *Uno de* | Ejecutar diariamente a una hora específica ("HH:MM"). |
| `weekly_days` | Lista[Cadena] | *Uno de* | Ejecutar en días específicos ("mon", "tue", ...) a la `time`. |
| `monthly_dates` | Lista[Entero] | *Uno de* | Ejecutar en fechas específicas (1-31) a la `time`. |
| `time` | Cadena | *Uno de* | Requerido para `weekly_days` y `monthly_dates`. Formato "HH:MM". |

**Nota sobre Zonas Horarias:** Todos los campos de tiempo se interpretan de acuerdo con la variable de entorno global `TZ` (por defecto "UTC").

### Ejemplo

```toml
[cleanup_job]
blueprint = "system_cleanup"
interval_seconds = 3600
input_data = { target = "temp_files" }

[daily_report]
blueprint = "generate_report"
daily_at = "09:00" # Se ejecuta a las 09:00 hora TZ
input_data = { type = "full_daily" }

[weekly_backup]
blueprint = "full_backup"
weekly_days = ["fri"]
time = "23:00"
input_data = { compression = "max" }
```

---

## Recarga Dinámica

Puedes actualizar `workers.toml` sin reiniciar el Orquestador.
Para hacer esto, envía una solicitud POST (con un token de cliente válido) al punto final:

`POST /api/v1/admin/reload-workers`

Esto obliga al Orquestador a volver a leer el archivo y actualizar los hashes de los tokens en Redis.

---

## Variables de Entorno

Además de los archivos de configuración, el Orquestador se configura a través de variables de entorno.

| Variable | Descripción | Predeterminado |
| :--- | :--- | :--- |
| `API_HOST` | Host para vincular el servidor API. | `0.0.0.0` |
| `API_PORT` | Puerto para vincular el servidor API. | `8080` |
| `ENABLE_CLIENT_API` | **Modo Holon Puro:** Si se establece en `false`, deshabilita la API de Cliente (`/api/v1/...`). El Orquestador solo aceptará tareas de un padre a través de RXON (Interfaz de Worker). | `true` |
| `REDIS_HOST` | Nombre de host del servidor Redis. Requerido para producción. | `""` (MemoryStorage) |
| `REDIS_PORT` | Puerto del servidor Redis. | `6379` |
| `REDIS_DB` | Índice de base de datos Redis. | `0` |
| `INSTANCE_ID` | **Importante para Escalado:** Identificador único para esta instancia del Orquestador. Utilizado como nombre de consumidor en Redis Streams. Por defecto es el nombre de host si no se establece. | `hostname` |
| `CLIENT_TOKEN` | Token global para clientes API (respaldo si no se usa `clients.toml`). | `secure-orchestrator-token` |
| `GLOBAL_WORKER_TOKEN` | Token global para workers (respaldo si no se usa `workers.toml`). | `secure-worker-token` |
| `WORKERS_CONFIG_PATH` | Ruta a `workers.toml`. | `""` |
| `CLIENTS_CONFIG_PATH` | Ruta a `clients.toml`. | `""` |
| `SCHEDULES_CONFIG_PATH` | Ruta a `schedules.toml`. | `""` |
| `BLUEPRINTS_DIR` | Ruta a un directorio con archivos de Python que contienen instancias de `StateMachineBlueprint` para ser cargadas automáticamente. | `""` |
| `TZ` | **Zona Horaria Global:** Afecta a los disparadores del programador, marcas de tiempo de logs y salida de API de historial (por ejemplo, "Europe/Madrid", "UTC"). | `UTC` |
| `LOG_LEVEL` | Nivel de registro (`DEBUG`, `INFO`, `WARNING`, `ERROR`). | `INFO` |
| `LOG_FORMAT` | Formato de registro (`text` o `json`). | `json` |
| `WORKER_TIMEOUT_SECONDS` | Tiempo máximo permitido para que un worker complete una tarea. | `300` |
| `WORKER_POLL_TIMEOUT_SECONDS` | Tiempo de espera para solicitudes de tareas de sondeo largo de workers. | `30` |
| `WORKER_HEALTH_CHECK_INTERVAL_SECONDS` | Intervalo para actualizar el TTL del worker (usado para comprobaciones de salud). | `60` |
| `JOB_MAX_RETRIES` | Número máximo de reintentos para fallos de tareas transitorios. | `3` |
| `WATCHER_INTERVAL_SECONDS` | Intervalo para que el proceso Watcher en segundo plano verifique trabajos con tiempo de espera agotado. | `20` |
| `EXECUTOR_MAX_CONCURRENT_JOBS` | Número máximo de trabajos concurrentes (manejadores) procesados por el Orquestador. | `100` |
| `HISTORY_DATABASE_URI` | URI para almacenamiento de historial (`sqlite:///...` o `postgresql://...`). | `""` (Deshabilitado) |
| `RATE_LIMITING_ENABLED` | Habilitar middleware de limitación de velocidad. | `true` |
| `RATE_LIMIT_LIMIT` | Número máximo predeterminado de solicitudes permitidas por período para puntos finales API generales. | `100` |
| `RATE_LIMIT_PERIOD` | Período de limitación de velocidad en segundos. | `60` |
| `RATE_LIMIT_HEARTBEAT_LIMIT` | Límite específico para solicitudes de latido de worker por período. | `120` |
| `RATE_LIMIT_POLL_LIMIT` | Límite específico para solicitudes de sondeo de tareas de worker por período. | `60` |

### Seguridad y TLS (mTLS)

Configura estas variables para habilitar HTTPS y TLS Mutuo (Confianza Cero).

| Variable | Descripción | Predeterminado |
| :--- | :--- | :--- |
| `TLS_ENABLED` | Habilitar servidor HTTPS. | `false` |
| `TLS_CERT_PATH` | Ruta al certificado SSL del servidor (`.crt`). Requerido si TLS está habilitado. | `""` |
| `TLS_KEY_PATH` | Ruta a la clave privada del servidor (`.key`). Requerido si TLS está habilitado. | `""` |
| `TLS_CA_PATH` | Ruta al paquete de certificados CA para verificar certificados de clientes. | `""` |
| `TLS_REQUIRE_CLIENT_CERT` | Si es `true`, el servidor rechazará conexiones sin un certificado de cliente válido (mTLS). | `false` |

### Almacenamiento S3 (Opcional)

Configura estas variables para habilitar la Descarga de Carga Útil en S3.

| Variable | Descripción | Predeterminado |
| :--- | :--- | :--- |
| `S3_ENDPOINT_URL` | URL del almacenamiento compatible con S3. **Requerido** para habilitar S3. | `""` |
| `S3_ACCESS_KEY` | ID de Clave de Acceso. **Requerido**. | `""` |
| `S3_SECRET_KEY` | Clave de Acceso Secreta. **Requerido**. | `""` |
| `S3_DEFAULT_BUCKET` | Nombre del bucket predeterminado para cargas útiles de trabajos. | `avtomatika-payloads` |
| `S3_REGION` | Región S3. | `us-east-1` |
| `S3_MAX_CONCURRENCY` | Número máximo de conexiones concurrentes a S3 en todos los trabajos. Previene el agotamiento de descriptores de archivos. | `100` |
| `S3_AUTO_CLEANUP` | Si es `true`, elimina automáticamente los archivos de S3 y los artefactos temporales locales cuando un trabajo se completa o falla. Establezca en `false` si desea gestionar la limpieza manualmente o mediante políticas de ciclo de vida de S3. | `true` |
| `TASK_FILES_DIR` | Directorio local para almacenamiento temporal de archivos durante la ejecución del trabajo. | `/tmp/avtomatika-payloads` |
