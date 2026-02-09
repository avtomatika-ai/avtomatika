[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/README.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/README.md)

# Avtomatika Orchestrator

[![Licencia: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/release/python-3110/)
[![Estilo de Código: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

Avtomatika es un motor potente y basado en estados para gestionar flujos de trabajo asíncronos complejos en Python. Proporciona un marco robusto para construir aplicaciones escalables y resilientes al separar la lógica del proceso de la lógica de ejecución.

Este documento sirve como una guía completa para desarrolladores que buscan construir pipelines (blueprints) e integrar el Orquestador en sus aplicaciones.

## Tabla de Contenidos
- [Concepto Principal: Orquestador, Blueprints y Workers](#concepto-principal-orquestador-blueprints-y-workers)
- [Instalación](#instalación)
- [Inicio Rápido: Uso como Librería](#inicio-rápido-uso-como-librería)
- [Conceptos Clave: JobContext y Actions](#conceptos-clave-jobcontext-y-actions)
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
  - [Observabilidad](#observabilidad)
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
from avtomatika import OrchestratorEngine, StateMachineBlueprint
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
my_blueprint = StateMachineBlueprint(
    name="my_first_blueprint",
    api_version="v1",
    api_endpoint="/jobs/my_flow"
)

# Usar inyección de dependencias para obtener solo los datos que necesitas.
@my_blueprint.handler_for("start", is_start=True)
async def start_handler(job_id: str, initial_data: dict, actions: ActionFactory):
    """El estado inicial para cada nuevo trabajo."""
    print(f"Job {job_id} | Inicio: {initial_data}")
    actions.transition_to("end")

# Todavía puedes solicitar el objeto de contexto completo si lo prefieres.
@my_blueprint.handler_for("end", is_end=True)
async def end_handler(context):
    """El estado final. El pipeline termina aquí."""
    print(f"Job {context.job_id} | Completo.")

# 3. Inicializar el Motor del Orquestador
engine = OrchestratorEngine(storage, config)
engine.register_blueprint(my_blueprint)

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
        print("
Deteniendo servidor.")
```

### Ciclo de Vida del Motor: `run()` vs. `start()`

El `OrchestratorEngine` ofrece dos formas de iniciar el servidor:

*   **`engine.run()`**: Este es un método simple y **bloqueante**. Es útil para scripts dedicados donde el orquestador es el único componente principal. Se encarga de iniciar y detener el servidor por ti. No debes usar esto dentro de una función `async def` que sea parte de una aplicación más grande, ya que puede entrar en conflicto con el bucle de eventos.

*   **`await engine.start()`** y **`await engine.stop()`**: Estos son los métodos no bloqueantes para integrar el motor en una aplicación `asyncio` más grande.
    *   `start()` configura e inicia el servidor web en segundo plano.
    *   `stop()` apaga el servidor con elegancia y limpia los recursos.
    El ejemplo de "Inicio Rápido" anterior demuestra la forma correcta de usar estos métodos.
## Argumentos del Manejador e Inyección de Dependencias

Los manejadores de estado son el núcleo de la lógica de tu flujo de trabajo. Avtomatika proporciona un potente sistema de inyección de dependencias para que escribir manejadores sea limpio y eficiente.

En lugar de recibir un único objeto `context` grande, tu manejador puede pedir exactamente lo que necesita como argumentos de función. El motor los proporcionará automáticamente.

Los siguientes argumentos se pueden inyectar por nombre:

*   **Desde el contexto principal del trabajo:**
    *   `job_id` (str): El ID del trabajo actual.
    *   `initial_data` (dict): Los datos con los que se creó el trabajo.
    *   `state_history` (dict): Un diccionario para almacenar y pasar datos entre pasos. Los datos devueltos por los workers se fusionan automáticamente en este diccionario.
    *   `actions` (ActionFactory): El objeto utilizado para decirle al orquestador qué hacer a continuación (por ejemplo, `actions.transition_to(...)`).
    *   `client` (ClientConfig): Información sobre el cliente API que inició el trabajo.
    *   `data_stores` (SimpleNamespace): Acceso a recursos compartidos como conexiones de base de datos o cachés.
*   **Desde los resultados del worker:**
    *   Cualquier clave de un diccionario devuelto por un worker anterior se puede inyectar por nombre.

### Ejemplo: Inyección de Dependencias

Esta es la forma recomendada de escribir manejadores.

```python
# Un worker para esta tarea devolvió: {"output_path": "/videos/123.mp4", "duration": 95}
# Este diccionario se fusionó automáticamente en `state_history`.

@my_blueprint.handler_for("publish_video")
async def publish_handler(
    job_id: str,
    output_path: str, # Inyectado desde state_history
    duration: int,    # Inyectado desde state_history
    actions: ActionFactory
):
    print(f"Job {job_id}: Publicando video en {output_path} ({duration}s).")
    actions.transition_to("complete")
```

### El Objeto `actions`

Este es el argumento inyectado más importante. Le dice al orquestador qué hacer a continuación. **Solo se puede llamar a un** método de `actions` en un solo manejador.

*   `actions.transition_to("next_state")`: Mueve el trabajo a un nuevo estado.
*   `actions.dispatch_task(...)`: Delega el trabajo a un Worker.
*   `actions.dispatch_parallel(...)`: Ejecuta múltiples tareas a la vez.
*   `actions.await_human_approval(...)`: Pausa el flujo de trabajo para recibir entrada externa.
*   `actions.run_blueprint(...)`: Inicia un flujo de trabajo secundario.

### Compatibilidad con Versiones Anteriores: El Objeto `context`

Para compatibilidad con versiones anteriores o si prefieres tener un solo objeto, aún puedes pedir `context`.

```python
# Este manejador es equivalente al de arriba.
@my_blueprint.handler_for("publish_video")
async def publish_handler_old_style(context):
    output_path = context.state_history.get("output_path")
    duration = context.state_history.get("duration")

    print(f"Job {context.job_id}: Publicando video en {output_path} ({duration}s).")
    context.actions.transition_to("complete")
```
## Conceptos Clave: JobContext y Actions

### Arquitectura de Alto Rendimiento

Avtomatika está diseñada para entornos de alta carga con miles de workers concurrentes.

*   **Despachador O(1)**: Utiliza intersecciones avanzadas de conjuntos de Redis para encontrar workers adecuados al instante.
*   **Seguridad de Confianza Cero**:
    *   **mTLS (TLS Mutuo)**: Autenticación mutua entre el Orquestador y los Workers mediante certificados.
    *   **STS (Servicio de Tokens de Seguridad)**: Mecanismo de rotación de tokens con tokens de acceso de corta duración.
    *   **Extracción de Identidad**: Asigna automáticamente el Nombre Común (CN) del certificado al ID del Worker.
*   **Integridad de Datos**:
    *   **Validación de Extremo a Extremo**: Verificación automática del tamaño del archivo y ETag (hash) durante las transferencias S3.
    *   **Pista de Auditoría**: Los metadatos de los archivos se registran en el historial para una trazabilidad completa.
*   **Capa de Protocolo**: Construido sobre `rxon`, un contrato estricto que define las interacciones, asegurando la compatibilidad futura y permitiendo la evolución del transporte (por ejemplo, a gRPC).
*   **E/S No Bloqueante**:
    *   **Webhooks**: Enviados a través de una cola de fondo limitada.
    *   **Streaming S3**: Uso de memoria constante independientemente del tamaño del archivo.

## Recetario de Blueprints: Características Clave

### 1. Transiciones Condicionales (`.when()`)

Usa `.when()` para crear ramas de lógica condicional. La cadena de condición es evaluada por el motor antes de llamar al manejador, por lo que aún usa el prefijo `context.`. El manejador en sí, sin embargo, puede usar inyección de dependencias.

```python
# La condición `.when()` aún se refiere a `context`.
@my_blueprint.handler_for("decision_step").when("context.initial_data.type == 'urgent'")
async def handle_urgent(actions):
    actions.transition_to("urgent_processing")

# El manejador predeterminado si ninguna condición `.when()` coincide.
@my_blueprint.handler_for("decision_step")
async def handle_normal(actions):
    actions.transition_to("normal_processing")
```

> **Nota sobre Limitaciones:** La versión actual de `.when()` utiliza un analizador simple con las siguientes limitaciones:
> *   **Sin Atributos Anidados:** Solo puedes acceder a campos directos de `context.initial_data` o `context.state_history` (por ejemplo, `context.initial_data.field`). Los objetos anidados (por ejemplo, `context.initial_data.area.field`) no son compatibles.
> *   **Solo Comparaciones Simples:** Solo se admiten los siguientes operadores: `==`, `!=`, `>`, `<`, `>=`, `<=`. No se permiten expresiones lógicas complejas con `AND`, `OR` o `NOT`.
> *   **Tipos de Valor Limitados:** El analizador solo reconoce cadenas (entre comillas), enteros y flotantes. Los valores booleanos (`True`, `False`) y `None` no se analizan correctamente y se tratarán como cadenas.

### 2. Delegar Tareas a Workers (`dispatch_task`)

Esta es la función principal para delegar trabajo. El orquestador pondrá en cola la tarea y esperará a que un worker la recoja y devuelva un resultado.

```python
@my_blueprint.handler_for("transcode_video")
async def transcode_handler(initial_data, actions):
    actions.dispatch_task(
        task_type="video_transcoding",
        params={"input_path": initial_data.get("path")},
        # Definir el siguiente paso basado en el estado de respuesta del worker
        transitions={
            "success": "publish_video",
            "failure": "transcoding_failed",
            "needs_review": "manual_review" # Ejemplo de un estado personalizado
        }
    )
```
Si el worker devuelve un estado no listado en `transitions`, el trabajo pasará automáticamente a un estado fallido.

### 3. Ejecución Paralela y Agregación (Fan-out/Fan-in)

Ejecuta múltiples tareas simultáneamente y reúne sus resultados.

```python
# 1. Fan-out: Despachar múltiples tareas para ser agregadas en un solo estado
@my_blueprint.handler_for("process_files")
async def fan_out_handler(initial_data, actions):
    tasks_to_dispatch = [
        {"task_type": "file_analysis", "params": {"file": file}})
        for file in initial_data.get("files", [])
    ]
    # Usa dispatch_parallel para enviar todas las tareas a la vez.
    # Todas las tareas exitosas conducirán implícitamente al estado 'aggregate_into'.
    actions.dispatch_parallel(
        tasks=tasks_to_dispatch,
        aggregate_into="aggregate_results"
    )

# 2. Fan-in: Recopilar resultados usando el decorador @aggregator_for
@my_blueprint.aggregator_for("aggregate_results")
async def aggregator_handler(aggregation_results, state_history, actions):
    # Este manejador solo se ejecutará DESPUÉS de que TODAS las tareas
    # despachadas por dispatch_parallel estén completas.

    # aggregation_results es un diccionario de {task_id: result_dict}
    summary = [res.get("data") for res in aggregation_results.values()]
    state_history["summary"] = summary
    actions.transition_to("processing_complete")
```

### 4. Inyección de Dependencias (DataStore)

Proporcionar a los manejadores acceso a recursos externos (como una caché o cliente de BD).

```python
import redis.asyncio as redis

# 1. Inicializar y registrar tu DataStore
redis_client = redis.Redis(decode_responses=True)
bp = StateMachineBlueprint(
    "blueprint_with_datastore",
    data_stores={"cache": redis_client}
)

# 2. Usarlo en un manejador mediante inyección de dependencias
@bp.handler_for("get_from_cache")
async def cache_handler(data_stores):
    # Acceder al redis_client por el nombre "cache"
    user_data = await data_stores.cache.get("user:123")
    print(f"Usuario desde caché: {user_data}")
```

### 5. Programador Nativo (Native Scheduler)

Avtomatika incluye un programador distribuido integrado. Te permite activar blueprints periódicamente (intervalo, diario, semanal, mensual) sin herramientas externas como cron.

*   **Configuración:** Definida en `schedules.toml`.
*   **Conciencia de Zona Horaria:** Soporta configuración global de zona horaria (por ejemplo, `TZ="Europe/Madrid"`).
*   **Bloqueo Distribuido:** Seguro para ejecutar con múltiples instancias del orquestador; se garantiza que los trabajos se ejecuten solo una vez por intervalo utilizando bloqueos distribuidos (Redis/Memoria).

```toml
# ejemplo de schedules.toml
[nightly_backup]
blueprint = "backup_flow"
daily_at = "02:00"
```

### 6. Notificaciones Webhook

El orquestador puede enviar notificaciones asíncronas a un sistema externo cuando un trabajo se completa, falla o entra en cuarentena. Esto elimina la necesidad de que los clientes consulten constantemente la API para actualizaciones de estado.

### 7. Descarga de Carga Útil en S3 (S3 Payload Offloading)

El orquestador proporciona soporte de primera clase para manejar archivos grandes a través de almacenamiento compatible con S3, impulsado por la biblioteca de alto rendimiento `obstore` (enlaces de Rust).

*   **Seguridad de Memoria (Streaming)**: Utiliza streaming para cargas y descargas, permitiendo el procesamiento de archivos más grandes que la RAM disponible sin errores OOM.
*   **Modo Gestionado**: El Orquestador gestiona el ciclo de vida del archivo (limpieza automática de objetos S3 y archivos temporales locales al finalizar el trabajo).
*   **Inyección de Dependencias**: Usa el argumento `task_files` en tus manejadores para leer/escribir datos fácilmente.
*   **Soporte de Directorios**: Soporta descarga y carga recursiva de directorios completos.

```python
@bp.handler_for("process_data")
async def process_data(task_files, actions):
    # Descarga por streaming de un archivo grande
    local_path = await task_files.download("large_dataset.csv")
    
    # ... procesar datos ...
    
    # Subir resultados
    await task_files.write_json("results.json", {"status": "done"})
    
    actions.transition_to("finished")
```

## Configuración de Producción
*   **Eventos:**
    *   `job_finished`: El trabajo alcanzó un estado final exitoso.
    *   `job_failed`: El trabajo falló (por ejemplo, debido a un error o entrada inválida).
    *   `job_quarantined`: El trabajo fue movido a cuarentena después de fallos repetidos.

**Ejemplo de Solicitud:**
```json
POST /api/v1/jobs/my_flow
{
    "initial_data": {
        "video_url": "..."
    },
    "webhook_url": "https://my-app.com/webhooks/avtomatika"
}
```

**Ejemplo de Carga Útil del Webhook:**
```json
{
    "event": "job_finished",
    "job_id": "123e4567-e89b-12d3-a456-426614174000",
    "status": "finished",
    "result": {
        "output_path": "/videos/result.mp4"
    },
    "error": null
}
```

## Configuración de Producción

El comportamiento del orquestador se puede configurar a través de variables de entorno. Además, cualquier parámetro de configuración cargado desde variables de entorno se puede anular programáticamente en el código de tu aplicación después de que se haya inicializado el objeto `Config`. Esto proporciona flexibilidad para diferentes escenarios de implementación y prueba.

**Importante:** El sistema emplea una **validación estricta** para los archivos de configuración (`clients.toml`, `workers.toml`) al inicio. Si un archivo de configuración no es válido (por ejemplo, TOML mal formado, campos obligatorios faltantes), la aplicación **fallará rápidamente** y saldrá con un error, en lugar de iniciarse en un estado parcialmente roto. Esto garantiza la seguridad y la integridad de la implementación.

### Archivos de Configuración

Para gestionar el acceso y la configuración de los workers de forma segura, Avtomatika utiliza archivos de configuración TOML.

-   **`clients.toml`**: Define los clientes API, sus tokens, planes y cuotas.
    ```toml
    [client_premium]
    token = "secret-token-123"
    plan = "premium"
    ```
-   **`workers.toml`**: Define tokens individuales para workers para mejorar la seguridad.
    ```toml
    [gpu-worker-01]
    token = "worker-secret-456"
    ```
-   **`schedules.toml`**: Define tareas periódicas (tipo CRON) para el programador nativo.
    ```toml
    [nightly_backup]
    blueprint = "backup_flow"
    daily_at = "02:00"
    ```

Para especificaciones detalladas y ejemplos, consulta la [**Guía de Configuración**](docs/es/configuration.md).

### Tolerancia a Fallos

El orquestador tiene mecanismos integrados para manejar fallos basados en el campo `error.code` en la respuesta de un worker.

*   **TRANSIENT_ERROR**: Un error temporal (por ejemplo, fallo de red). El orquestador reintentará automáticamente la tarea varias veces.
*   **RESOURCE_EXHAUSTED_ERROR / TIMEOUT_ERROR / INTERNAL_ERROR**: Tratados como errores transitorios y reintentados.
*   **PERMANENT_ERROR**: Un error permanente. La tarea será enviada inmediatamente a cuarentena.
*   **SECURITY_ERROR / DEPENDENCY_ERROR**: Tratados como errores permanentes (por ejemplo, violación de seguridad o modelo faltante). Cuarentena inmediata.
*   **INVALID_INPUT_ERROR**: Un error en los datos de entrada. Todo el pipeline (Job) se moverá inmediatamente al estado fallido.

### Seguimiento del Progreso

Los workers pueden informar el progreso de ejecución en tiempo real (0-100%) y mensajes de estado. Esta información es persistida automáticamente por el Orquestador y expuesta a través de la API de Estado del Trabajo (`GET /api/v1/jobs/{job_id}`).

### Concurrencia y Rendimiento

Para prevenir la sobrecarga del sistema durante el tráfico alto, el Orquestador implementa un mecanismo de contrapresión para su lógica interna de procesamiento de trabajos.

*   **`EXECUTOR_MAX_CONCURRENT_JOBS`**: Limita el número de manejadores de trabajos que se ejecutan simultáneamente dentro del proceso del Orquestador (predeterminado: `100`). Si se alcanza este límite, los nuevos trabajos permanecen en la cola de Redis hasta que haya un espacio disponible. Esto asegura que el bucle de eventos permanezca receptivo incluso con una acumulación masiva de trabajos pendientes.

### Alta Disponibilidad y Bloqueo Distribuido

La arquitectura soporta escalado horizontal. Múltiples instancias del Orquestador pueden ejecutarse detrás de un balanceador de carga.

*   **API Sin Estado:** La API no tiene estado; todo el estado se persiste en Redis.
*   **Identidad de Instancia:** Cada instancia debe tener un `INSTANCE_ID` único (por defecto es el nombre del host) para el manejo correcto de los grupos de consumidores de Redis Streams.
*   **Bloqueo Distribuido:** Los procesos en segundo plano (`Watcher`, `ReputationCalculator`) utilizan bloqueos distribuidos (vía Redis `SET NX`) para coordinar y prevenir condiciones de carrera cuando hay múltiples instancias activas.

### Log y Observabilidad

Avtomatika está diseñada para pilas de observabilidad modernas (ELK, Loki, Prometheus).

*   **Log Estructurado:** Por defecto, los logs se emiten en formato JSON, lo que facilita su análisis e indexación. Se puede cambiar a texto mediante `LOG_FORMAT="text"`.
*   **Conciencia de Zona Horaria:** Todas las marcas de tiempo de los logs respetan la variable de entorno `TZ` configurada globalmente.
*   **Trazabilidad:** Los logs incluyen `job_id`, `worker_id` y `task_id` para un seguimiento completo de extremo a extremo.
*   **Métricas:** Las métricas de Prometheus están disponibles en `/_public/metrics`, incluido un contador específico `orchestrator_ratelimit_blocked_total` para rastrear solicitudes bloqueadas.

### Limitación de Velocidad (Rate Limiting)

El Orquestador incluye un limitador de velocidad granular integrado basado en Redis para proteger contra abusos y DDoS.

*   **Protección Granular:** Los límites se aplican por Token de Cliente (para clientes API) o por ID de Worker (para workers).
*   **Consciente del Contexto:** Se aplican diferentes límites a diferentes operaciones:
    *   **Latidos (Heartbeats):** Límite más alto (por defecto 120/min) para permitir actualizaciones de estado frecuentes.
    *   **Sondeo (Polling):** Límite moderado (por defecto 60/min) para la obtención de tareas.
    *   **API General:** Límite predeterminado (por defecto 100/min) para otras operaciones.
*   **Aplicación Global:** El middleware se aplica globalmente, protegiendo todos los puntos de entrada, incluidas la API de Worker y la API de Cliente.

### Backend de Almacenamiento

Por defecto, el motor utiliza almacenamiento en memoria. Para producción, debes configurar almacenamiento persistente a través de variables de entorno.

*   **Redis (StorageBackend)**: Para almacenar estados actuales de trabajos (serializados con `msgpack`) y gestionar colas de tareas (usando Redis Streams con grupos de consumidores).
    *   Instalar:
        ```bash
        pip install "avtomatika[redis]"
        ```
    *   Configurar:
        ```bash
        export REDIS_HOST=tu_host_redis
        ```

*   **PostgreSQL/SQLite (HistoryStorage)**: Para archivar el historial de trabajos completados.
    *   Instalar:
        ```bash
        pip install "avtomatika[history]"
        ```
    *   Configurar:
        ```bash
        export HISTORY_DATABASE_URI=...
        ```
        *   SQLite: `sqlite:///ruta/a/history.db`
        *   PostgreSQL: `postgresql://user:pass@host/db`

### Seguridad

El orquestador utiliza tokens para autenticar solicitudes API.

*   **Autenticación de Cliente**: Todos los clientes API deben proporcionar un token en el encabezado `X-Client-Token`. El orquestador valida este token contra las configuraciones de clientes.
*   **Autenticación de Worker**: Los workers deben proporcionar un token en el encabezado `X-Worker-Token`.
    *   `GLOBAL_WORKER_TOKEN`: Puedes establecer un token global para todos los workers usando esta variable de entorno. Para desarrollo y pruebas, por defecto es `"secure-worker-token"`.
    *   **Tokens Individuales**: Para producción, se recomienda definir tokens individuales para cada worker en un archivo de configuración separado y proporcionar su ruta a través de la variable de entorno `WORKERS_CONFIG_PATH`. Los tokens de este archivo se almacenan en un formato hash por seguridad.

> **Nota sobre Recarga Dinámica:** El archivo de configuración de workers se puede recargar sin reiniciar el orquestador enviando una solicitud `POST` autenticada al punto final `/api/v1/admin/reload-workers`. Esto permite actualizaciones dinámicas de tokens de workers.

### Modo Holon Puro
Para entornos de alta seguridad o cuando se opera como un Holon Compuesto dentro de una HLN, puedes deshabilitar la API pública de cliente.
*   **Habilitar/Deshabilitar**: Establece `ENABLE_CLIENT_API="false"` (por defecto: `true`).
*   **Efecto**: El Orquestador dejará de escuchar en `/api/v1/jobs/...`. Solo aceptará tareas a través del Protocolo de Worker (RXON) de su padre.

### Observabilidad

Cuando se instala con la dependencia de telemetría, el sistema proporciona automáticamente:

*   **Métricas de Prometheus**: Disponibles en el punto final `/_public/metrics`.
*   **Rastreo Distribuido**: Compatible con OpenTelemetry y sistemas como Jaeger o Zipkin.
## Guía para Contribuidores

### Configuración del Entorno

*   Clona el repositorio.
*   **Para desarrollo local**, instala primero el paquete de protocolo:
    ```bash
    pip install -e ../rxon
    ```
*   Luego instala el motor en modo editable con todas las dependencias:
    ```bash
    pip install -e ".[all,test]"
    ```
*   Asegúrate de tener instaladas las dependencias del sistema, como `graphviz`.
    *   Debian/Ubuntu:
        ```bash
        sudo apt-get install graphviz
        ```
    *   macOS (Homebrew):
        ```bash
        brew install graphviz
        ```

### Ejecución de Pruebas

Para ejecutar el conjunto de pruebas de `avtomatika`:
```bash
pytest tests/
```

### Documentación API Interactiva

Avtomatika proporciona una página de documentación API interactiva integrada (similar a Swagger UI) que se genera automáticamente en función de tus blueprints registrados.

*   **Punto final:** `/_public/docs`
*   **Características:**
    *   **Lista de todos los puntos finales del sistema:** Documentación detallada para grupos de API Públicos, Protegidos y de Worker.
    *   **Documentación Dinámica de Blueprints:** Genera y lista automáticamente la documentación para todos los blueprints registrados en el motor, incluyendo sus puntos finales API específicos.
    *   **Pruebas Interactivas:** Te permite probar llamadas a la API directamente desde el navegador. Puedes proporcionar tokens de autenticación, parámetros y cuerpos de solicitud para ver respuestas reales del servidor.

## Documentación Detallada

Para profundizar en el sistema, consulta los siguientes documentos:

- [**Guía de Arquitectura**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/architecture.md): Una descripción detallada de los componentes del sistema y sus interacciones.
- [**Referencia de API**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/api_reference.md): Especificación completa de la API HTTP.
- [**Guía de Despliegue**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/deployment.md): Instrucciones para desplegar con Gunicorn/Uvicorn y NGINX.
- [**Recetario**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/cookbook/README.md): Ejemplos y mejores prácticas para crear blueprints.
