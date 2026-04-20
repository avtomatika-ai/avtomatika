[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/README.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/README.md)

# Avtomatika Orchestrator

[![Licencia: MPL 2.0](https://img.shields.io/badge/License-MPL%202.0-brightgreen.svg)](https://opensource.org/licenses/MPL-2.0)
[![Versión de PyPI](https://img.shields.io/pypi/v/avtomatika.svg)](https://pypi.org/project/avtomatika/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/release/python-3110/)

**Avtomatika** es un orquestador de alto rendimiento basado en máquinas de estados, diseñado para gestionar flujos de trabajo de IA de larga duración y tareas distribuidas. Actúa como el núcleo de una **Red Lógica Jerárquica (HLN)**, coordinando **Holones** (workers) a través del protocolo **RXON**.

## 🚀 Características Principales

- **State-Machine Driven**: Blueprints declarativos en Python para lógica compleja.
- **Almacenamiento Redis de Alto Rendimiento**: Indexación **ZSET** para gestión atómica de workers y serialización **Msgpack**.
- **Seguridad Zero Trust**: Autenticación obligatoria de workers y **Protección contra Reenvío** (ventana de tiempo de 60s).
- **Almacenamiento de Blobs Enchufable**: Soporte completo para S3 a través de la interfaz `BlobProvider` (potenciado por `obstore`).
- **Lógica Jerárquica**: Soporte para blueprints hijos (Ghosts) y ejecución paralela con agregación de resultados.
- **Observability**: Seguimiento distribuido con **OpenTelemetry** y métricas en tiempo real.

Este documento sirve como una guía completa para desarrolladores que buscan construir pipelines (blueprints) e integrar el Orquestador en sus aplicaciones.


## Tabla de Contenidos
- [Concepto Principal: Orquestador, Blueprints y Workers](#concepto-principal-orquestador-blueprints-y-workers)
- [Instalación](#instalación)
- [Inicio Rápido: Uso como Librería](#inicio-rápido-uso-como-librería)
- [Conceptos Clave: JobContext y Actions](#conceptos-clave-jobcontext-and-actions)
- [Recetario de Blueprints: Características Clave](#recetario-de-blueprints-características-clave)
  - [Transiciones Condicionales (.when())](#transiciones-condicionales-when)
  - [Delegar Tareas a Workers (dispatch_task)](#delegar-tareas-a-workers-dispatch_task)
  - [Ejecución Paralela y Agregación (Fan-out/Fan-in)](#ejecución-paralela-y-agregación-fan-outfan-in)
  - [Inyección de Dependencias (DataStore)](#inyección-de-dependencias-datastore)
  - [Programador Nativo (Scheduler)](#programador-nativo-scheduler)
  - [Descarga de Carga Útil en S3 (S3 Payload Offloading)](#descarga-de-carga-útil-en-s3-s3-payload-offloading)
  - [Notificaciones Webhook](#notificaciones-webhook)
- [Configuración de Producción](#configuración-de-producción)
  - [Tolerancia a Fallos](#tolerancia-a-fallos)
  - [Backend de Almacenamiento](#backend-de-almacenamiento)
  - [Observabilidad](#observability)
- [Guía para Contribuidores](#guía-para-contribuidores)
  - [Configuración del Entorno](#configuración-del-entorno)
  - [Ejecución de Pruebas](#ejecución-de-pruebas)

## Concepto Principal: Orquestador, Blueprints y Workers

El proyecto se basa en un patrón arquitectónico simple pero poderoso que separa la lógica del proceso de la lógica de ejecución.

*   **Orquestador (OrchestratorEngine)** — El Director. Gestiona todo el proceso de principio a fin, rastrea el estado, maneja errores y decide qué debe suceder a continuación. No realiza tareas comerciales por sí mismo.
*   **Blueprints (Blueprint)** — El Guion. Cada blueprint es un plan detallado (una máquina de estados) para un proceso comercial específico. Describe los pasos (estados) y las reglas para la transición entre ellos.
*   **Workers (Worker)** — El Equipo de Especialistas. Estos son ejecutores independientes y especializados. Cada worker sabe cómo realizar un conjunto específico de tareas (por ejemplo, "procesar video", "enviar correo electrónico") e informa al Orquestador.

## Ecosistema

Avtomatika es parte de un ecosistema más grande:

*   **[Protocolo Avtomatika](https://github.com/avtomatika-ai/rxon)**: Paquete compartido que contiene definiciones de protocolo, modelos de datos y utilidades que garantizan la coherencia en todos los componentes.
*   **[SDK de Worker de Avtomatika](https://github.com/avtomatika-ai/avtomatika-worker)**: El SDK oficial de Python para construir workers que se conectan a este motor.
*   **[Protocolo HLN](https://github.com/avtomatika-ai/hln)**: La especificación arquitectónica y el manifiesto detrás del sistema (Hierarchical Logic Network).
*   **[Ejemplo Completo](https://github.com/avtomatika-ai/avtomatika-full-example)**: Un proyecto de referencia completo que demuestra el motor y los workers en acción.

## Instalación

*   **Instalar solo el núcleo del motor:**
    ```bash
    pip install avtomatika
    ```

*   **Instalar con soporte para Redis (recomendado para producción):**
    ```bash
    pip install "avtomatika[redis]"
    ```

*   **Instalar con soporte para almacenamiento de historial (SQLite, PostgreSQL):**
    ```bash
    pip install "avtomatika[history]"
    ```

*   **Instalar con soporte de telemetría (Prometheus, OpenTelemetry):**
    ```bash
    pip install "avtomatika[telemetry]"
    ```

*   **Instalar con soporte para S3 (Descarga de Carga Útil):**
    ```bash
    pip install "avtomatika[s3]"
    ```

*   **Instalar todas las dependencias, incluidas las pruebas:**
    ```bash
    pip install "avtomatika[all,test]"
    ```
## Inicio Rápido: Uso como Librería

Puedes integrar y ejecutar fácilmente el motor del orquestador dentro de tu propia aplicación.

```python
# mi_app.py
import asyncio
from avtomatika import OrchestratorEngine, Blueprint
from avtomatika.context import ActionFactory
from avtomatika.storage import MemoryStorage
from avtomatika.config import Config

# 1. Configuración General
storage = MemoryStorage()
config = Config() # Carga la configuración desde variables de entorno

# Establecer tokens explícitamente para este ejemplo
# El token del cliente debe enviarse en el encabezado 'X-Client-Token'.
config.CLIENT_TOKEN = "my-secret-client-token"
# El token del worker debe enviarse en el encabezado 'X-Worker-Token'.
config.GLOBAL_WORKER_TOKEN = "my-secret-worker-token"

# 2. Definir el Blueprint del Flujo de Trabajo
bp = Blueprint(
    name="bp",
    api_version="v1",
    api_endpoint="/jobs/my_flow"
)

# Usar inyección de dependencias para obtener solo los datos que necesitas.
@bp.handler(is_start=True)
async def start(job_id: str, initial_data: dict, actions: ActionFactory):
    """El estado inicial para cada nuevo trabajo."""
    print(f"Job {job_id} | Inicio: {initial_data}")
    actions.go_to("end")

# Todavía puedes solicitar el objeto de contexto completo si lo prefieres.
@bp.handler(is_end=True)
async def end(context):
    """El estado final. El pipeline termina aquí."""
    print(f"Job {context.job_id} | Completo.")

# 3. Inicializar el Motor del Orquestador
engine = OrchestratorEngine(storage, config)
engine.register_blueprint(bp)

# 4. Acceso a Componentes (Opcional)
# Puedes acceder a la app aiohttp interna y a los componentes principales usando AppKeys
# from avtomatika.app_keys import ENGINE_KEY, DISPATCHER_KEY
# app = engine.app
# dispatcher = app[DISPATCHER_KEY]

# 5. Definir el punto de entrada principal para ejecutar el servidor
async def main():
    await engine.start()
    
    try:
        await asyncio.Event().wait()
    finally:
        await engine.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDeteniendo servidor.")
```

### Ciclo de Vida del Motor: `run()` vs. `start()`

El `OrchestratorEngine` ofrece dos formas de iniciar el servidor:

*   **`engine.run()`**: Este es un método simple y **bloqueante**. Se encarga de iniciar y detener el servidor por ti. No debes usar esto dentro de una función `async def` que sea parte de una aplicación más grande.

*   **`await engine.start()`** y **`await engine.stop()`**: Estos son los métodos no bloqueantes para integrar el motor en una aplicación `asyncio` más grande.
    *   `start()` configura e inicia el servidor web en segundo plano.
    *   `stop()` apaga el servidor con elegancia y limpia los recursos.
## Handler Arguments e Inyección de Dependencias

State handlers son el núcleo de la lógica de tu flujo de trabajo. Avtomatika proporciona un potente sistema de inyección de dependencias.

> **Consejo:** El nombre del estado en `@bp.handler()` y `@bp.aggregator()` ahora es opcional. Si se omite, se utilizará el nombre de la función.

Los siguientes argumentos se pueden inyectar por nombre:

*   **Desde el contexto principal del trabajo:**
    *   `job_id` (str): El ID del trabajo actual.
    *   `initial_data` (dict): Los datos iniciales.
    *   `state_history` (dict): Diccionario de datos entre pasos.
    *   `actions` (ActionFactory): Control de flujo del orquestador.
    *   `client` (ClientConfig): Información del cliente API.
    *   `data_stores` (SimpleNamespace): Acceso a recursos compartidos.
*   **Desde los resultados del worker:**
    *   Cualquier clave de un diccionario devuelto por un worker anterior se puede inyectar por nombre.

### Ejemplo: Inyección de Dependencias

```python
@bp.handler
async def publish_video(
    job_id: str,
    output_path: str, # Inyectado desde state_history
    duration: int,    # Inyectado desde state_history
    actions: ActionFactory
):
    print(f"Job {job_id}: Publicando video en {output_path} ({duration}s).")
    actions.go_to("complete")
```

### El Objeto `actions`

*   `actions.go_to("next_state")`: Mueve el trabajo a un nuevo estado.
*   `actions.dispatch_task(...)`: Delega el trabajo a un Worker.
*   `actions.dispatch_parallel(...)`: Ejecuta múltiples tareas a la vez.
*   `actions.await_human_approval(...)`: Pausa para entrada externa.
*   `actions.run_blueprint(...)`: Inicia un flujo secundario.

## Conceptos Clave: JobContext y Actions

### Arquitectura de Alto Rendimiento

Avtomatika está diseñada para entornos de alta carga con miles de workers concurrentes.

*   **Smart Matching Unificado (RXON v1.0b7)**:
    *   **Matching Unificado**: Migración a la lógica de selección formalizada de `rxon`. Todos los recursos (CPU, RAM, GPU, etc.) se rigen estrictamente por el estándar del protocolo HLN.
    *   **Comparación Numérica Inteligente**: Realiza automáticamente verificaciones **GE (Mayor o Igual)** para números.
    *   **Hot Cache y Skills**: Prioriza workers que ya tienen modelos de IA específicos cargados.
    *   **Deep Schema Matching**: Prioriza a los workers cuyos esquemas coinciden con la tarea.
    *   **Overflow Strategy**: Desvío automático a workers costosos si los económicos están saturados.
    *   **Work Stealing**: Los trabajadores inactivos pueden robar tareas atómicamente a velocidad O(1).
    *   **Load Balancing**: Incrementos de carga optimistas entre latidos (heartbeats).
    *   **Networking Eficiente**: TCP Keep-Alive y compresión Zstd/Gzip.
    *   **Logging Asíncrono**: Procesamiento no bloqueante vía `QueueHandler`.
    *   **IO Optimizado**: Serialización pesada en hilos e índices de DB para el historial.
*   **Reputación Autoregulada**:
    *   **Penalizaciones**: Reducción inmediata por violaciones de contrato (-0.2) o fallos (-0.05).
    *   **Trusted Guard**: Parámetro `REPUTATION_MIN_THRESHOLD` para ignorar nodos poco fiables.
*   **Arquitectura basada en Contratos**:
    *   **Validación de API**: Verificación de `initial_data` antes de crear el trabajo.
    *   **Validación de Resultados**: Verificación de las respuestas de los workers contra su esquema.
*   **Heartbeats Bi-direccionales**: Canal de comunicación robusto con Jitter para evitar tormentas de peticiones.
*   **Seguridad de Confianza Cero**: 
    *   **Verificación de Cadena de Identidad**: Valida la ruta completa ("bubbling path") para eventos en holarquías profundas mediante firmas digitales.
    *   **mTLS y STS**: mTLS y STS para rotación de tokens de acceso.
*   **E/S No Bloqueante**:
    *   **Webhooks**: Enviados vía un pool paralelo de workers de fondo.
    *   **Streaming S3**: Uso de memoria constante independientemente del tamaño del archivo.

## Recetario de Blueprints: Características Clave

### 1. Transiciones Condicionales (`.when()`)

```python
@bp.handler().when("context.initial_data.type == 'urgent'")
async def decision_step(actions):
    actions.go_to("urgent_processing")
```

### 2. Delegar Tareas a Workers (`dispatch_task`)

```python
@bp.handler
async def transcode_video(initial_data, actions):
    actions.dispatch_task(
        task_type="video_transcoding",
        params={"input_path": initial_data.get("path")},
        transitions={"success": "publish_video", "failure": "retry"}
    )
```

### 3. Ejecución Paralela y Agregación (Fan-out/Fan-in)

```python
actions.dispatch_parallel(tasks=tasks_to_dispatch, aggregate_into="aggregate_results")

@bp.aggregator
async def aggregate_results(aggregation_results, state_history, actions):
    summary = [res.get("data") for res in aggregation_results.values()]
    state_history["summary"] = summary
    actions.go_to("complete")
```

### 4. Inyección de Dependencias (DataStore)

```python
bp = Blueprint("bp", data_stores={"cache": redis_client})

@bp.handler
async def get_from_cache(data_stores):
    user_data = await data_stores.cache.get("user:123")
```

### 5. Programador Nativo (Scheduler)

*   **Configuración:** `schedules.toml`.
*   **Bloqueo Distribuido:** Seguro para múltiples instancias vía Redis.

```toml
[nightly_backup]
blueprint = "backup_flow"
daily_at = "02:00"
dispatch_timeout = 60
```

### 6. Notificaciones Webhook

Notificaciones asíncronas para `job_finished`, `job_failed` o `job_quarantined`.

### 7. Descarga de Carga Útil en S3

*   **Streaming**: Uso eficiente de memoria vía `obstore` (Rust).
*   **Gestionado**: Limpieza automática de objetos S3.

```python
@bp.handler
async def process_data(task_files, actions):
    local_path = await task_files.download("large_dataset.csv")
    await task_files.write_json("results.json", {"status": "done"})
    actions.go_to("finished")
```

## Grupos de API y Versiones

Todos los endpoints externos están prefijados con `/api/v1/`.

**Ejemplo de Solicitud:**
```json
POST /api/v1/jobs/my_flow
{
    "initial_data": { "video_url": "..." },
    "webhook_url": "https://my-app.com/webhooks/avtomatika",
    "dispatch_timeout": 30
}
```

## Configuración de Producción

### Archivos de Configuración

-   **`clients.toml`**: Clientes API, tokens y cuotas.
-   **`workers.toml`**: Tokens individuales para workers.
-   **`schedules.toml`**: Tareas periódicas.

Para más detalles, consulta la [**Guía de Configuración**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/configuration.md).

### Concurrencia y Rendimiento

*   **`EXECUTOR_MAX_CONCURRENT_JOBS`**: Límite de manejadores internos (default: `1000`).
*   **`WATCHER_LIMIT`**: Reacción ante таймауты por ciclo (default: `500`).
*   **`DISPATCHER_MAX_CANDIDATES`**: Límite de chequeos de cumplimiento (default: `50`).

### Observabilidad

*   **Structured JSON Logging**: Vía `QueueHandler` no bloqueante.
*   **Métricas**: En `/_public/metrics`, con prefijo `orchestrator_` y `orchestrator_loop_lag_seconds`.

### Capa de Almacenamiento

*   **Redis**: Estados (`msgpack`) y colas (Streams).
*   **PostgreSQL/SQLite**: Historial con índices optimizados.

### Modo Holon Puro
Establece `ENABLE_CLIENT_API="false"` para deshabilitar la API pública y solo aceptar tareas vía RXON.

## Guía para Contribuidores

```bash
pip install -e ../rxon
pip install -e ".[all,test]"
pytest tests/
```

### Documentación API Interactiva
Disponible en `/_public/docs`.

## Documentación Detallada

- [**Guía de Arquitectura**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/architecture.md)
- [**Referencia de API**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/api_reference.md)
- [**Guía de Configuración**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/configuration.md)
- [**Guía de Despliegue**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/deployment.md)
- [**Cookbook**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/cookbook/README.md)
