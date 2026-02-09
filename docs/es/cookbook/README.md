[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/README.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/cookbook/README.md)

# Recetario: Recetas para la Librería del Orquestador

Este recetario proporciona una colección de recetas para construir flujos de trabajo (blueprints) con Avtomatika Orchestrator.

> **Nota de Producción:** Los ejemplos a continuación usan `print()` para simplificar y claridad. En un entorno de producción, debes usar el módulo estándar `logging` de Python. El Orquestador configura automáticamente el registro JSON estructurado, y el uso de `logging.info(...)` asegura que tus registros estén formateados y capturados correctamente.

## Tabla de Contenidos

### **Receta 1: Creación de un Pipeline Lineal Simple**

**Tarea:** Crear un pipeline que ejecute secuencialmente tres pasos: A -> B -> C.
```python
from orchestrator.blueprint import StateMachineBlueprint

simple_pipeline = StateMachineBlueprint(
    name="simple_linear_flow",
    api_version="v1",
    api_endpoint="jobs/simple_flow"
)

@simple_pipeline.handler_for("start", is_start=True)
async def start_handler(context, actions):
    actions.transition_to("step_A")

@simple_pipeline.handler_for("step_A")
async def handler_A(context, actions):
    actions.dispatch_task(
        task_type="simple_task",
        params=context.initial_data,
        transitions={"success": "step_B", "failure": "failed"}
    )

@simple_pipeline.handler_for("step_B")
async def handler_B(context, actions):
    actions.transition_to("finished")

@simple_pipeline.handler_for("finished", is_end=True)
async def finished_handler(context, actions):
    print(f"Pipeline {context.job_id} completado exitosamente.")

@simple_pipeline.handler_for("failed", is_end=True)
async def failed_handler(context, actions):
    print(f"Pipeline {context.job_id} falló.")
```

### **Receta 2: Implementación de "Humano en el Bucle" (Moderación)**

**Tarea:** Pausar el pipeline después del paso `generate_data` y esperar la aprobación de un moderador.
```python
from orchestrator.blueprint import StateMachineBlueprint

moderation_pipeline = StateMachineBlueprint(
    name="moderation_flow",
    api_version="v1",
    api_endpoint="jobs/moderation_flow"
)

@moderation_pipeline.handler_for("generate_data", is_start=True)
async def generate_data_handler(context, actions):
    actions.dispatch_task(
        task_type="data_generation",
        params=context.initial_data,
        transitions={"success": "awaiting_approval", "failure": "failed"}
    )


@moderation_pipeline.handler_for("process_approved_data", is_end=True)
async def process_approved_handler(context, actions):
    actions.transition_to("finished")

@moderation_pipeline.handler_for("rejected_by_moderator", is_end=True)
async def process_rejected_handler(context, actions):
    print(f"El trabajo {context.job_id} fue rechazado por el moderador.")

@moderation_pipeline.handler_for("failed", is_end=True)
async def moderation_failed_handler(context, actions):
    print(f"El trabajo {context.job_id} falló.")

@moderation_pipeline.handler_for("awaiting_approval")
async def await_approval_handler(context, actions):
    actions.await_human_approval(
        integration="telegram",
        message=f"Se requiere aprobación para el trabajo {context.job_id}",
        transitions={
            "approved": "process_approved_data",
            "rejected": "rejected_by_moderator"
        }
    )

@moderation_pipeline.handler_for("process_approved_data")
async def process_approved_handler(context, actions):
    actions.transition_to("finished")
```

### **Receta 3: Ejecución Paralela y Agregación de Resultados**

**Tarea:** Ejecutar múltiples tareas independientes (por ejemplo, procesar diferentes partes de un archivo) simultáneamente y, después de esperar a que todas se completen, recopilar sus resultados en un solo resumen.

**Concepto:**
El paralelismo se logra llamando a `actions.dispatch_task()` múltiples veces en un manejador.
1.  **Lanzamiento:** En un manejador regular, llama a `dispatch_task` para cada tarea paralela. **Requisito clave:** todas estas llamadas deben apuntar al mismo estado en `transitions`. Este estado será el punto de agregación.
2.  **Agregación:** El manejador para el estado agregador está marcado con el decorador especial `@blueprint.aggregator_for(...)`. El Orquestador no llamará a este manejador hasta que **todas** las tareas que conducen a este estado se completen.
3.  **Acceso a Resultados:** Dentro del manejador agregador, los resultados de todas las tareas paralelas están disponibles en `context.aggregation_results`. Este es un diccionario donde la clave es `task_id` y el valor es el resultado devuelto por el worker.

```python
from orchestrator.blueprint import StateMachineBlueprint
import logging

logger = logging.getLogger(__name__)

parallel_bp = StateMachineBlueprint(
    name="parallel_flow_example",
    api_version="v1",
    api_endpoint="/jobs/parallel_example"
)

@parallel_bp.handler_for("start", is_start=True)
async def start_parallel_tasks(context, actions):
    """
    Este manejador inicia dos tareas que se ejecutarán en paralelo.
    Ambas tareas, al completarse exitosamente, harán la transición del proceso al estado 'aggregate_results'.
    """
    logger.info(f"Trabajo {context.job_id}: Iniciando tareas paralelas.")

    # Iniciar tarea A
    actions.dispatch_task(
        task_type="task_A",
        params={"input": "data_for_A"},
        transitions={"success": "aggregate_results", "failure": "failed"}
    )

    # Iniciar tarea B
    actions.dispatch_task(
        task_type="task_B",
        params={"input": "data_for_B"},
        transitions={"success": "aggregate_results", "failure": "failed"}
    )

@parallel_bp.aggregator_for("aggregate_results")
async def aggregate_results_handler(context, actions):
    """
    Este manejador se llamará solo después de que tanto task_A como task_B se completen exitosamente.
    """
    logger.info(f"Trabajo {context.job_id}: Agregando resultados.")

    processed_results = {}
    # context.aggregation_results contiene resultados de ambas tareas
    for task_id, result in context.aggregation_results.items():
        if result.get("status") == "success":
            processed_results[task_id] = result.get("data")

    context.state_history["aggregated_data"] = processed_results
    logger.info(f"Trabajo {context.job_id}: Datos agregados: {processed_results}")
    actions.transition_to("end")

@parallel_bp.handler_for("end", is_end=True)
async def end_flow(context, actions):
    final_data = context.state_history.get("aggregated_data")
    logger.info(f"Trabajo {context.job_id}: Proceso completo. Datos finales: {final_data}")

@parallel_bp.handler_for("failed", is_end=True)
async def failed_handler(context, actions):
    logger.error(f"Trabajo {context.job_id} falló.")

```
*Nota: Si al menos una de las tareas paralelas falla (transita al estado `failed`), el manejador agregador no será llamado y todo el `Job` transitará inmediatamente al estado `failed`.*

### **Receta 4: Configuración de Worker para Múltiples Orquestadores**

**Tarea:** Asegurar alta disponibilidad y/o equilibrio de carga configurando un solo Worker para conectarse a múltiples instancias de Orquestador.

**Concepto:**
`worker_sdk` admite dos modos para trabajar con múltiples Orquestadores, configurados a través de variables de entorno. El worker se registrará automáticamente y enviará latidos a todos los Orquestadores en la lista.

-   `FAILOVER` (predeterminado): El worker sondea a los Orquestadores en el orden en que aparecen en la configuración. Si el Orquestador principal no está disponible, el Worker cambia automáticamente al siguiente en la lista.
-   `ROUND_ROBIN`: El worker sondea a cada Orquestador en la lista secuencialmente, permitiendo la distribución de carga.

**Cómo configurar:**
1.  **`ORCHESTRATORS_CONFIG`**: En lugar de `ORCHESTRATOR_URL`, usa esta variable para pasar una cadena JSON que describa todos los Orquestadores disponibles.
2.  **`MULTI_ORCHESTRATOR_MODE`**: Establece el valor en `FAILOVER` o `ROUND_ROBIN`.

#### **Ejemplo 1: Configuración para Alta Disponibilidad (Failover)**

```bash
# El worker sondeará 'orchestrator-main'.
# Si cae, el Worker cambia automáticamente a 'orchestrator-backup'.
export ORCHESTRATORS_CONFIG='[
    {"url": "http://orchestrator-main:8080"},
    {"url": "http://orchestrator-backup:8080"}
]'

export MULTI_ORCHESTRATOR_MODE="FAILOVER"

# Ejecutar worker
python -m your_worker_module
```

#### **Ejemplo 2: Configuración para Equilibrio de Carga (Round Robin)**
```bash
# El worker sondeará 'orchestrator-1' y 'orchestrator-2' secuencialmente.
export ORCHESTRATORS_CONFIG='[
    {"url": "http://orchestrator-1:8080"},
    {"url": "http://orchestrator-2:8080"}
]'

export MULTI_ORCHESTRATOR_MODE="ROUND_ROBIN"

# Ejecutar worker
python -m your_worker_module
```
*Nota: Esta configuración se realiza exclusivamente en el lado del Worker y es completamente transparente para el Orquestador. Cada Orquestador ve este Worker como un ejecutor normal registrado.*

### **Receta 5: Enrutamiento Condicional con .when()**

```python
from orchestrator.blueprint import StateMachineBlueprint

multilingual_pipeline = StateMachineBlueprint(
    name="multilingual_flow",
    api_version="v1",
    api_endpoint="jobs/multilingual_flow"
)

@multilingual_pipeline.handler_for("start", is_start=True)
async def start_multilingual(context, actions):
    # Este paso solo pasa el control más adelante, donde se activará la lógica condicional
    actions.transition_to("process_text")

@multilingual_pipeline.handler_for("process_text").when("context.initial_data.language == 'en'")
async def process_english_text(context, actions):
    actions.dispatch_task(task_type="process_en", params=context.initial_data, transitions={"success": "finished"})

@multilingual_pipeline.handler_for("process_text").when("context.initial_data.language == 'de'")
async def process_german_text(context, actions):
    actions.dispatch_task(task_type="process_de", params=context.initial_data, transitions={"success": "finished"})

@multilingual_pipeline.handler_for("finished", is_end=True)
async def multilingual_finished(context, actions):
    print(f"Trabajo {context.job_id} terminó el procesamiento.")
```

### **Receta 6: Elección de Estrategia de Despacho de Tareas**

**Tarea:** Para una tarea crítica, usar no solo un worker aleatorio, sino el menos cargado.

```python
from orchestrator.blueprint import StateMachineBlueprint

critical_pipeline = StateMachineBlueprint(
    name="critical_flow",
    api_version="v1",
    api_endpoint="jobs/critical_flow"
)

@critical_pipeline.handler_for("start", is_start=True)
async def start_critical_task(context, actions):
    actions.dispatch_task(
        task_type="critical_computation",
        params=context.initial_data,
        # Especificar estrategia de selección de worker
        dispatch_strategy="least_connections",
        transitions={"success": "finished"}
    )

@critical_pipeline.handler_for("finished", is_end=True)
async def critical_finished(context, actions):
    print(f"Tarea crítica {context.job_id} terminada.")
```
*Nota: Estrategias disponibles: `default`, `round_robin`, `least_connections`.*

### **Receta 7: Gestión de Prioridad de Tareas**

**Tarea:** El sistema tiene tareas normales y urgentes que deben ejecutarse primero, incluso si llegaron más tarde.

**Solución:** El método `dispatch_task` acepta un parámetro `priority`, que es un flotante. Mayor valor significa mayor prioridad.

```python
from orchestrator.blueprint import StateMachineBlueprint

priority_pipeline = StateMachineBlueprint(
    name="priority_flow",
    api_version="v1",
    api_endpoint="jobs/priority_flow"
)

@priority_pipeline.handler_for("start", is_start=True)
async def start_priority_task(context, actions):
    # Asignar prioridad diferente dependiendo de los datos de entrada
    is_urgent = context.initial_data.get("is_urgent", False)

    actions.dispatch_task(
        task_type="computation",
        params=context.initial_data,
        # Las tareas urgentes obtienen prioridad 10, otras - 0
        priority=10.0 if is_urgent else 0.0,
        transitions={"success": "finished"}
    )

@priority_pipeline.handler_for("finished", is_end=True)
async def priority_finished(context, actions):
    print(f"Tarea {context.job_id} terminada.")

```
*Nota: Si múltiples tareas tienen la misma prioridad, se ejecutarán en orden de llegada (FIFO) dentro de esa prioridad.*


### **Receta 8: Optimización de Costos con `cheapest` y `max_cost`**

**Tarea:** Despachar tarea al worker más barato, pero solo si su costo no excede un cierto umbral.

```python
from orchestrator.blueprint import StateMachineBlueprint

cost_optimized_pipeline = StateMachineBlueprint(
    name="cost_optimized_flow",
    api_version="v1",
    api_endpoint="jobs/cost_optimized_flow"
)

@cost_optimized_pipeline.handler_for("start", is_start=True)
async def start_cost_optimized_task(context, actions):
    actions.dispatch_task(
        task_type="image_compression",
        params=context.initial_data,
        # Seleccionar worker más barato
        dispatch_strategy="cheapest",
        # Pero solo si su costo ($/seg) no es más de 0.05
        max_cost=0.05,
        transitions={"success": "finished", "failure": "too_expensive"}
    )

@cost_optimized_pipeline.handler_for("finished", is_end=True)
async def cost_optimized_finished(context, actions):
    print("Trabajo terminado con optimización de costos.")

@cost_optimized_pipeline.handler_for("too_expensive", is_end=True)
async def cost_optimized_failed(context, actions):
    print("El trabajo falló porque ningún worker cumplió con los criterios de costo.")
```
*Nota: La estrategia `cheapest` utiliza el campo `cost_per_second` del worker. Si ningún worker cumple con `max_cost`, el pipeline no encontrará un ejecutor y fallará.*

### **Receta 9: Uso de Almacenes de Datos (`data_stores`)**

**Tarea:** Usar almacenamiento persistente compartido (`data_store`) para intercambiar datos entre diferentes estados o incluso diferentes ejecuciones del mismo pipeline. Por ejemplo, para implementar un contador o caché.

**Concepto:**
1.  **Inicialización:** Al crear un blueprint, puedes "adjuntar" uno o más almacenes de datos a él. Cada almacén es esencialmente un envoltorio de Redis que proporciona acceso clave-valor.
2.  **Acceso en Manejadores:** Dentro de cualquier manejador de este blueprint, puedes acceder a estos almacenes a través de `context.data_stores`.
3.  **Persistencia:** Los datos en `data_store` persisten entre llamadas a manejadores e incluso entre diferentes `job_id` del mismo blueprint.

```python
from orchestrator.blueprint import StateMachineBlueprint

# 1. Crear blueprint y agregar data_store llamado 'request_counter' a él.
#    También podemos establecer valores iniciales.
analytics_bp = StateMachineBlueprint(
    name="analytics_flow",
    api_version="v1",
    api_endpoint="jobs/analytics"
)
analytics_bp.add_data_store("request_counter", {"total_requests": 0})


@analytics_bp.handler_for("start", is_start=True)
async def count_request(context, actions):
    # 2. Acceder al almacén a través del contexto e incrementar contador.
    #    Las operaciones son atómicas ya que Redis es de un solo hilo.
    current_count = await context.data_stores.request_counter.get("total_requests")
    new_count = current_count + 1
    await context.data_stores.request_counter.set("total_requests", new_count)

    # Guardar valor actual del contador en el historial de este trabajo específico
    context.state_history["request_number"] = new_count

    actions.transition_to("finished")

@analytics_bp.handler_for("finished", is_end=True)
async def show_result(context, actions):
    request_num = context.state_history.get("request_number")
    print(f"Trabajo {context.job_id} procesado. Número de solicitud {request_num}.")

```

**Cómo funciona:**

-   `analytics_bp.add_data_store("request_counter", ...)` crea una instancia de `AsyncDictStore` que vive tanto como viva el Orquestador.
-   `context.data_stores.request_counter` proporciona acceso a esta instancia. `data_stores` es un objeto dinámico cuyos atributos corresponden a los nombres de los almacenes creados.
-   Cada vez que ejecutes este pipeline (`/v1/jobs/analytics`), incrementará **el mismo** contador porque `data_store` está vinculado al blueprint, no al `job_id` específico.

### **Receta 10: Cancelación de una Tarea en Ejecución**

**Tarea:** Ejecutar una tarea de larga duración (por ejemplo, procesamiento de video) y poder cancelar su ejecución a través de la API.

**Concepto:**
El sistema soporta un mecanismo de cancelación híbrido que funciona con o sin WebSocket.
1.  **Solicitud de Cancelación:** Envías una solicitud `POST` al punto final de la API `/api/v1/jobs/{job_id}/cancel`.
2.  **Establecimiento de Bandera:** El Orquestador establece inmediatamente una bandera en Redis señalando la solicitud de cancelación.
3.  **Notificación Push (WebSocket):** Si el Worker está conectado vía WebSocket, el Orquestador envía adicionalmente el comando `cancel_task` para una reacción inmediata.
4.  **Verificación Pull (Redis):** Si el Worker no usa WebSocket o la conexión se pierde temporalmente, debe verificar periódicamente la bandera en Redis usando `worker.check_for_cancellation(task_id)`.
5.  **Reacción:** Al recibir la señal de cancelación (por cualquier método), el Worker debe interrumpir el trabajo y devolver un resultado con estado `"cancelled"`.
6.  **Finalización:** El pipeline transita al estado especificado en `transitions` para el estado `"cancelled"`.

#### **Paso 1: Código del Worker**
El Worker debe llamar periódicamente a `worker.check_for_cancellation`.

```python
# my_worker.py
@worker.task("long_video_processing")
async def process_video(params: dict, task_id: str, job_id: str) -> dict:
    total_frames = 1000
    for frame in range(total_frames):
        # ... lógica de procesamiento de fotogramas ...
        await asyncio.sleep(0.1) # Simular trabajo

        # Verificar parada cada 100 fotogramas
        if frame % 100 == 0:
            if await worker.check_for_cancellation(task_id):
                print(f"Tarea {task_id} cancelada.")
                return {"status": "cancelled"}

    return {"status": "success"}
```

#### **Paso 2: Creación del Blueprint**
El blueprint debe tener una transición para el nuevo estado `cancelled`.
```python
from orchestrator.blueprint import StateMachineBlueprint

cancellable_pipeline = StateMachineBlueprint(
    name="cancellable_flow",
    api_version="v1",
    api_endpoint="jobs/cancellable"
)

@cancellable_pipeline.handler_for("start", is_start=True)
async def start_long_task(context, actions):
    actions.dispatch_task(
        task_type="long_video_processing",
        params=context.initial_data,
        transitions={
            "success": "finished_successfully",
            "failure": "task_failed",
            "cancelled": "task_cancelled" # Nueva transición
        }
    )

@cancellable_pipeline.handler_for("finished_successfully", is_end=True)
async def success_handler(context, actions):
    print(f"Trabajo {context.job_id} terminado exitosamente.")

@cancellable_pipeline.handler_for("task_failed", is_end=True)
async def failure_handler(context, actions):
    print(f"Trabajo {context.job_id} falló.")

@cancellable_pipeline.handler_for("task_cancelled", is_end=True)
async def cancelled_handler(context, actions):
    print(f"Trabajo {context.job_id} cancelado exitosamente.")
```

#### **Paso 3: Ejecutar Trabajo**
```bash
# Ejecutar Trabajo y obtener su ID
curl -X POST http://localhost:8080/api/v1/jobs/cancellable 
-H "Content-Type: application/json" 
-H "X-Client-Token: your-secret-orchestrator-token" 
-d '{"video_url": "..."}'
# Respuesta: {"status": "accepted", "job_id": "YOUR_JOB_ID"}
```

#### **Paso 4: Cancelar Trabajo**
```bash
# Enviar solicitud de cancelación usando el job_id recibido
curl -X POST http://localhost:8080/api/v1/jobs/YOUR_JOB_ID/cancel 
-H "X-Client-Token: your-secret-orchestrator-token"
```
Verás el mensaje de cancelación en los logs del Worker, y el `Job` en el Orquestador transitará al estado `task_cancelled`.

### **Receta 11: Envío de Progreso de Tarea**

**Tarea:** Para una tarea de larga duración (por ejemplo, entrenamiento de modelos), informar regularmente el progreso para ser rastreado en la UI.

**Concepto:**
Al igual que la cancelación, esta característica funciona vía WebSocket. El Worker puede enviar eventos de progreso, que el Orquestador guarda en `state_history` del `Job` correspondiente.

#### **Paso 1: Código en Worker**
El Worker debe llamar periódicamente a `worker.send_progress()`.
```python
# Dentro de tu archivo de worker (my_worker.py)
@worker.task("train_model")
async def train_model_handler(params: dict, task_id: str, job_id: str) -> dict:
    for epoch in range(10):
        # ... lógica de entrenamiento ...
        await asyncio.sleep(5)
        # Enviar progreso después de cada época
        await worker.send_progress(
            task_id=task_id,
            job_id=job_id,
            progress=(epoch + 1) / 10,
            message=f"Época {epoch + 1} completada"
        )
    return {"status": "success"}
```

#### **Paso 2: Verificación en Orquestador**
Después de la ejecución de la tarea, puedes solicitar el estado y ver `progress_updates` en `state_history`:
```json
{
  "id": "YOUR_JOB_ID",
  "current_state": "finished",
  "state_history": {
    "progress_updates": [
      {"progress": 0.1, "message": "Época 1 completada", "timestamp": "..."},
      {"progress": 0.2, "message": "Época 2 completada", "timestamp": "..."}
    ]
  }
}
```

### **Receta 12a: Trabajo con Archivos S3 en Orquestador (TaskFiles)**

**Tarea:** Leer un archivo de configuración desde S3 dentro de un manejador del Orquestador para decidir el enrutamiento, o escribir un pequeño archivo de resultado de vuelta a S3.

**Concepto:**
Cuando el soporte S3 está habilitado en el Orquestador, puedes solicitar el argumento `task_files` en tus manejadores. Este objeto proporciona métodos auxiliares para interactuar con la carpeta S3 del trabajo (`jobs/{job_id}/`) sin gestionar conexiones manualmente.

**Prerrequisito:**
- Orquestador configurado con `S3_ENDPOINT_URL`, etc.
- Dependencias instaladas: `pip install avtomatika[s3]`

```python
from orchestrator.blueprint import StateMachineBlueprint

s3_ops_bp = StateMachineBlueprint(
    name="s3_ops_flow",
    api_version="v1",
    api_endpoint="jobs/s3_ops"
)

@s3_ops_bp.handler_for("check_config", is_start=True)
async def check_config_handler(context, task_files, actions):
    """
    Descarga 'config.json' desde S3 (jobs/{job_id}/config.json),
    lo lee y decide el siguiente paso.
    """
    if not task_files:
        # S3 podría estar deshabilitado en la configuración
        actions.transition_to("failed")
        return

    try:
        # read_json descarga automáticamente el archivo si no está presente localmente
        config = await task_files.read_json("config.json")
    except Exception:
        actions.transition_to("config_missing")
        return

    if config.get("mode") == "fast":
        actions.transition_to("fast_processing")
    else:
        actions.transition_to("deep_processing")

@s3_ops_bp.handler_for("fast_processing")
async def fast_process(context, task_files, actions):
    # ... lógica ...
    
    # Escribir resultado de vuelta a S3
    await task_files.write_text("result.txt", "Hecho rápido.")
    
    # O subir un directorio completo recursivamente
    # await task_files.download("dataset/") # Descarga s3://.../dataset/ a local
    # await task_files.upload("output_folder") # Sube carpeta local a s3://.../output_folder/

    actions.transition_to("finished")
```

### **Receta 12b: Trabajo con Archivos Grandes vía S3 (Lado del Worker)**

**Tarea:** Procesar un archivo de video grande. Pasarlo directamente en JSON no es práctico, así que usaremos S3 para el intercambio de datos.

**Concepto:**
El SDK del Worker tiene soporte S3 integrado. Si los parámetros de la tarea (`params`) contienen un valor que comienza con `s3://`, el SDK descarga automáticamente el archivo al directorio temporal y sustituye el URI con la ruta local. De manera similar, si tu manejador devuelve una ruta de archivo local, el SDK la sube a S3 y devuelve el URI `s3://` al Orquestador.

**Prerrequisitos:**
- Instalar dependencia `aioboto3`: `pip install orchestrator-worker[s3]`
- Configurar variables de entorno para acceso a S3:
  ```bash
  export S3_ENDPOINT_URL="http://your-s3-host:9000"
  export S3_ACCESS_KEY_ID="your-access-key"
  export S3_SECRET_ACCESS_KEY="your-secret-key"
  export S3_BUCKET_NAME="my-processing-bucket"
  ```

#### **Paso 1: Código en Worker**
El Worker no necesita saber sobre S3. Simplemente trabaja con archivos locales.

```python
# my_video_worker.py
import os
from pathlib import Path

@worker.task("process_video_from_s3")
async def process_video(params: dict, **kwargs) -> dict:
    # El SDK ya descargó el archivo y pasó la ruta local
    local_video_path = Path(params["video_path"])

    if not local_video_path.exists():
        return {"status": "failure", "error_type": "INVALID_INPUT_ERROR", "error": "Archivo de entrada no encontrado"}

    # ... lógica de procesamiento de video ...
    # Crear archivo de salida
    output_path = local_video_path.parent / f"processed_{local_video_path.name}"
    with open(output_path, "w") as f:
        f.write("datos de video procesados")

    # Solo devolver la ruta local. El SDK la subirá a S3 por sí mismo.
    return {"status": "success", "data": {"processed_video_path": str(output_path)}}
```

#### **Paso 2: Creación del Blueprint**
El Blueprint simplemente pasa el URI S3 como parámetro.
```python
@s3_pipeline.handler_for("start", is_start=True)
async def start_s3_task(context, actions):
    actions.dispatch_task(
        task_type="process_video_from_s3",
        # Pasar URI S3
        params={"video_path": context.initial_data.get("s3_uri")},
        transitions={"success": "finished"}
    )
```

#### **Paso 3: Ejecutar Tarea**
```bash
curl -X POST ... -d '{"s3_uri": "s3://my-bucket/raw_videos/movie.mp4"}'
```
Después de la ejecución, `state_history` de la tarea contendrá el resultado con el nuevo URI S3, por ejemplo: `{"processed_video_path": "s3://my-processing-bucket/processed_movie.mp4"}`.

### **Receta 13: Gestión de Lógica de Reintento con Tipos de Error**

**Tarea:** Crear un worker que distinga fallos temporales (por ejemplo, indisponibilidad de API externa) de errores permanentes (por ejemplo, formato de entrada inválido).

**Concepto:**
El Orquestador por defecto reintenta cualquier tarea fallida (`TRANSIENT_ERROR`). Sin embargo, si el Worker devuelve el tipo de error `PERMANENT_ERROR` o `INVALID_INPUT_ERROR`, el Orquestador no perderá tiempo reintentando.

- `PERMANENT_ERROR`: Tarea movida inmediatamente a cuarentena para análisis manual.
- `INVALID_INPUT_ERROR`: Tarea marcada inmediatamente como `failed`.

#### **Paso 1: Código en Worker**
```python
# my_api_worker.py
@worker.task("fetch_external_data")
async def fetch_data(params: dict, **kwargs) -> dict:
    api_key = params.get("api_key")
    if not api_key:
        # Entrada inválida, inútil reintentar
        return {"status": "failure", "error_type": "INVALID_INPUT_ERROR", "error": "Falta la clave API"}

    try:
        # ... intentar llamada a API externa ...
        response = await call_flaky_api(api_key)
        return {"status": "success", "data": response}
    except APITimeoutError:
        # API temporalmente no disponible, vale la pena reintentar
        return {"status": "failure", "error_type": "TRANSIENT_ERROR", "error": "API agotó tiempo de espera"}
    except APIAuthError:
        # Clave API inválida, problema permanente
        return {"status": "failure", "error_type": "PERMANENT_ERROR", "error": "Clave API inválida"}

```

#### **Paso 2: Creación del Blueprint**
El Blueprint puede tener diferentes ramas para diferentes resultados.
```python
@error_handling_bp.handler_for("start", is_start=True)
async def start_api_call(context, actions):
    actions.dispatch_task(
        task_type="fetch_external_data",
        params=context.initial_data,
        transitions={
            "success": "finished_successfully",
            "failure": "handle_failure" # Manejador común para todos los errores
        }
    )

@error_handling_bp.handler_for("handle_failure", is_end=True)
async def failure_handler(context, actions):
    # Aquí podemos analizar `job_state` para entender si la tarea fue puesta en cuarentena o simplemente falló.
    job_state = await context.storage.get_job_state(context.job_id)
    if job_state.get("status") == "quarantined":
        print(f"Trabajo {context.job_id} en cuarentena debido a un error permanente.")
    else:
        print(f"Trabajo {context.job_id} falló.")
```

### **Receta 14: Blueprints Anidados (Sub-blueprints)**

**Tarea:** Crear un pipeline principal que use otro pipeline reutilizable como paso y obtenga su resultado.

**Concepto:**
El mecanismo de blueprint anidado permite que un pipeline (padre) ejecute otro (hijo) y espere su finalización. El resultado del blueprint hijo (éxito o fallo) se guarda automáticamente en `state_history` del pipeline padre. Esto permite crear flujos de trabajo complejos pero modulares y reutilizables.

```python
from orchestrator.blueprint import StateMachineBlueprint

# 1. Primero define un blueprint pequeño y reutilizable.
#    No tiene punto final de API, solo puede ser ejecutado desde otro blueprint.
text_processing_bp = StateMachineBlueprint(name="text_processor")

@text_processing_bp.handler_for("start", is_start=True)
async def process(context, actions):
    # ... alguna lógica de procesamiento de texto ...
    text = context.initial_data.get("text", "")
    if not text:
        # Si la entrada es inválida, terminar el blueprint con fallo.
        actions.transition_to("failed")
    else:
        # El resultado se puede guardar en `state_history` del blueprint hijo.
        # Aunque no se pasa directamente, está disponible para depuración en el historial.
        context.state_history["processed_text"] = text.upper()
        actions.transition_to("finished")

@text_processing_bp.handler_for("finished", is_end=True)
async def text_processing_finished(context, actions):
    pass

@text_processing_bp.handler_for("failed", is_end=True)
async def text_processing_failed(context, actions):
    pass


# 2. Ahora crea el pipeline principal llamándolo.
main_pipeline = StateMachineBlueprint(
    name="main_flow",
    api_version="v1",
    api_endpoint="jobs/main_flow"
)

@main_pipeline.handler_for("start", is_start=True)
async def parent_start(context, actions):
    actions.transition_to("process_user_text")

@main_pipeline.handler_for("process_user_text")
async def main_handler(context, actions):
    user_text = context.initial_data.get("raw_text", "")
    actions.run_blueprint(
        blueprint_name="text_processor",
        initial_data={"text": user_text},
        transitions={"success": "final_step", "failure": "sub_job_failed"}
    )

@main_pipeline.handler_for("final_step")
async def final_step_handler(context, actions):
    # Resultado del blueprint hijo guardado en `state_history`.
    # Clave generada automáticamente. Podemos encontrarla iterando claves.
    sub_job_result = None
    for key, value in context.state_history.items():
        if key.startswith("sub_job_") and "outcome" in value:
            sub_job_result = value
            break

    if sub_job_result:
        print(f"Sub-blueprint terminó con resultado: {sub_job_result['outcome']}")
    else:
        print("Resultado de sub-blueprint no encontrado en historial de estado.")

    actions.transition_to("finished")

@main_pipeline.handler_for("sub_job_failed")
async def sub_job_failed_handler(context, actions):
    print("Sub-blueprint falló, manejando fallo en padre.")
    actions.transition_to("failed")

@main_pipeline.handler_for("finished", is_end=True)
async def main_finished(context, actions):
    pass

@main_pipeline.handler_for("failed", is_end=True)
async def main_failed(context, actions):
    pass
```

### **Receta 15: Visualización de Lógica de Blueprint**

**Tarea:** Analizar o documentar un pipeline complejo generando automáticamente su esquema visual.

**Solución:** Cada objeto `StateMachineBlueprint` tiene un método integrado `.render_graph()` que utiliza `graphviz` para crear un diagrama de estado y transición. Analiza el código de tus manejadores para encontrar llamadas a `actions.transition_to()` y `actions.dispatch_task()` y construye el gráfico basado en ellas.

**Prerrequisito:**
**Graphviz** debe estar instalado en tu sistema para que esta función funcione.
-   **Debian/Ubuntu:** `sudo apt-get install graphviz`
-   **macOS (Homebrew):** `brew install graphviz`
-   **Windows:** Instalar desde el sitio oficial y agregar al `PATH`.

**Ejemplo:**
```python
from orchestrator.blueprint import StateMachineBlueprint

# Tomar pipeline de lógica condicional de otra receta
conditional_pipeline = StateMachineBlueprint(name="conditional_flow")

@conditional_pipeline.handler_for("start", is_start=True)
async def start(context, actions):
    actions.transition_to("process_data")

@conditional_pipeline.handler_for("process_data").when("context.initial_data.type == 'A'")
async def process_a(context, actions):
    actions.dispatch_task(task_type="task_a", transitions={"success": "finished", "failure": "failed"})

@conditional_pipeline.handler_for("process_data").when("context.initial_data.type == 'B'")
async def process_b(context, actions):
    actions.dispatch_task(task_type="task_b", transitions={"success": "finished", "failure": "failed"})

@conditional_pipeline.handler_for("finished", is_end=True)
async def finished(context, actions):
    print("Terminado.")

@conditional_pipeline.handler_for("failed", is_end=True)
async def failed(context, actions):
    print("Fallado.")

# Ahora generar su diagrama
if __name__ == "__main__":
    # Este comando crea 'conditional_flow_diagram.png' en el directorio actual
    conditional_pipeline.render_graph("conditional_flow_diagram", format="png")
```
Ejecutar este script crea una imagen que muestra claramente todas las rutas de ejecución posibles en este blueprint.

### **Receta 16: Actualización Parcial del Estado del Worker (PATCH)**

**Tarea:** El Worker necesita actualizar solo un campo en su estado (por ejemplo, carga actual) sin enviar todos los metadatos (ID, capacidades, costo, etc.).

**Solución:** La API del Orquestador admite el método `PATCH` para el punto final `/_worker/workers/{worker_id}/status`, perfecto para actualizaciones parciales. Ahorra tráfico y sigue las mejores prácticas REST.

```python
# Ejemplo de código de worker enviando solo datos cambiados
async def update_load(session, new_load):
    await session.patch(
        "http://orchestrator.host/_worker/workers/my-worker-id/status",
        json={"load": new_load}
    )
```

### **Receta 17: Gestión de Cuotas y Configuración de Clientes**

**Tarea:** Configurar diferentes límites de uso (cuotas) para diferentes clientes y usar sus parámetros personalizados dentro de blueprints.

**Concepto:**
1.  **Configuración:** Toda la información del cliente, incluido token, plan y cuotas, se define en `clients.toml`.
2.  **Carga al Inicio:** Al inicio, el Orquestador lee este archivo y carga los datos de cuota en Redis.
3.  **Verificación de Cuota:** El Middleware de Cuota especial verifica y decrementa automáticamente el contador de "intentos" para cada solicitud API. Si se agotan los intentos, la solicitud se rechaza con `429 Too Many Requests`.
4.  **Acceso a Parámetros:** Dentro del manejador del blueprint, puedes acceder a todos los parámetros estáticos del cliente (por ejemplo, plan o lista de idiomas) a través de `context.client.config`.

#### **Paso 1: Configuración de `clients.toml`**
```toml
[client_premium]
token = "user_token_vip"
plan = "premium"
monthly_attempts = 100000
languages = ["en", "de", "fr"]

[client_free]
token = "user_token_free"
plan = "free"
monthly_attempts = 100
languages = ["en"]
```

#### **Paso 2: Uso en Blueprint**
```python
from orchestrator.blueprint import StateMachineBlueprint

premium_features_bp = StateMachineBlueprint(
    name="premium_flow",
    api_version="v1",
    api_endpoint="jobs/premium_flow"
)

@premium_features_bp.handler_for("start", is_start=True)
async def start_premium_flow(context, actions):
    # Obtener configuración del cliente que hizo la solicitud
    client_config = context.client.config

    # Usar parámetros del cliente para lógica condicional
    if client_config.get("plan") == "premium":
        # Usar worker más potente para clientes premium
        actions.dispatch_task(
            task_type="high_quality_generation",
            params=context.initial_data,
            transitions={"success": "finished"}
        )
    elif "en" in client_config.get("languages", []):
         # Worker estándar para clientes gratuitos de habla inglesa
        actions.dispatch_task(
            task_type="standard_quality_generation",
            params=context.initial_data,
            transitions={"success": "finished"}
        )
    else:
        # Error para otros
        actions.transition_to("failed", error_message="Idioma no soportado para su plan")

@premium_features_bp.handler_for("finished", is_end=True)
async def premium_finished(context, actions):
    pass

@premium_features_bp.handler_for("failed", is_end=True)
async def premium_failed(context, actions):
    pass
```

### **Receta 18: Mejores Prácticas para Manejadores `is_start` e `is_end`**

**Tarea:** Entender qué código es mejor colocar en los manejadores iniciales y finales para crear pipelines limpios y fiables.

#### **Propósito del Manejador `is_start=True`**

El manejador inicial es la "puerta de entrada" de tu pipeline. Lugar ideal para:
1.  **Validación y Preparación de Datos:** Verificar que todos los datos necesarios estén presentes y preparar `state_history` para los pasos siguientes.
2.  **Enrutamiento Inicial:** Decidir el primer paso real basado en los datos de entrada.

**Ejemplo:**
```python
from orchestrator.blueprint import StateMachineBlueprint

validation_bp = StateMachineBlueprint(
    name="validation_example",
    api_version="v1",
    api_endpoint="jobs/validation"
)

@validation_bp.handler_for("validate_input", is_start=True)
async def validate_input_handler(context, actions):
    """
    Valida datos de entrada y decide a dónde dirigir el proceso.
    """
    user_id = context.initial_data.get("user_id")
    document_type = context.initial_data.get("document_type")

    if not user_id or not document_type:
        actions.transition_to("invalid_input_failed")
        return

    # Guardar datos validados en state_history para usar en otros manejadores
    context.state_history["validated_user"] = user_id
    context.state_history["doc_type"] = document_type

    # Enrutamiento basado en tipo de documento
    if document_type == "invoice":
        actions.transition_to("process_invoice")
    else:
        actions.transition_to("process_other_document")

# ... (otros manejadores) ...

@validation_bp.handler_for("invalid_input_failed", is_end=True)
async def invalid_input_handler(context, actions):
    print(f"El trabajo {context.job_id} falló debido a entrada inválida.")

```

#### **Propósito de los Manejadores `is_end=True`**

Los manejadores finales son la "salida" de tu pipeline. Realizan acciones finales y **no deben** contener llamadas a `actions.transition_to()` o `dispatch_task()`.

**Usos Clave:**
1.  **Registro Final y Notificación:** Registrar resultado, enviar correo electrónico o mensaje de Slack.
2.  **Limpieza de Recursos:** Eliminar archivos temporales creados durante el proceso.
3.  **Manejo de Resultados:** Puedes tener múltiples estados finales para diferentes resultados (éxito, fallo, rechazo, etc.).

**Ejemplo:**
```python
from orchestrator.blueprint import StateMachineBlueprint
import os

finalization_bp = StateMachineBlueprint(name="finalization_example")

# ... (pasos intermedios que conducen a diferentes resultados) ...

@finalization_bp.handler_for("cleanup_and_notify_success", is_end=True)
async def success_handler(context, actions):
    """
    Ejecutado al completar exitosamente.
    """
    temp_file = context.state_history.get("temp_file_path")
    if temp_file and os.path.exists(temp_file):
        os.remove(temp_file)
        print(f"Trabajo {context.job_id}: Archivo temporal {temp_file} eliminado.")

    # send_success_email(context.initial_data.get("user_email"))
    print(f"Trabajo {context.job_id} terminado exitosamente. Notificación enviada.")


@finalization_bp.handler_for("handle_rejection", is_end=True)
async def rejection_handler(context, actions):
    """
    Ejecutado si el proceso fue rechazado.
    """
    rejection_reason = context.state_history.get("rejection_reason", "No se proporcionó razón")
    print(f"Trabajo {context.job_id} fue rechazado. Razón: {rejection_reason}")
    # send_rejection_notification(context.initial_data.get("user_email"), rejection_reason)

```
Usar `is_start` e `is_end` de esta manera hace que tus pipelines sean más estructurados, fiables y fáciles de entender.

### **Receta 19: Enrutamiento de Tareas Basado en Requisitos de Recursos**

**Tarea:** La tarea de generación de video (`video_montage`) requiere una GPU potente. Asegurar que la tarea se envíe a un worker equipado con al menos NVIDIA T4.

**Solución:** El método `dispatch_task` en `ActionFactory` acepta el parámetro `resource_requirements`, permitiendo especificar los requisitos mínimos de recursos del worker. El Despachador filtra automáticamente a los workers que no cumplen con estos requisitos.

```python
from orchestrator.blueprint import StateMachineBlueprint

gpu_intensive_pipeline = StateMachineBlueprint(
    name="gpu_intensive_flow",
    api_version="v1",
    api_endpoint="jobs/gpu_flow"
)

@gpu_intensive_pipeline.handler_for("start")
async def start_gpu_task(context, actions):
    actions.dispatch_task(
        task_type="video_montage",
        params=context.initial_data,
        # El despachador busca un worker cuyo `gpu_info.model` contenga "NVIDIA T4"
        # y tenga el modelo requerido instalado
        resource_requirements={
            "gpu": {
                "model": "NVIDIA T4",
                "vram_gb": 16
            },
            "installed_models": [
                "stable-diffusion-1.5"
            ]
        },
        transitions={"success": "finished", "failure": "failed"}
    )
```

### **Receta 20: Rastreo de Extremo a Extremo con OpenTelemetry**

**Tarea:** Rastrear la ruta de ejecución completa de un trabajo, desde la creación hasta el procesamiento del worker y la finalización.

**Solución:** El Orquestador gestiona automáticamente el contexto de rastreo de OpenTelemetry. El contexto se crea al recibir la solicitud API, se pasa al worker con la tarea y se devuelve con el resultado. Permite combinar todas las operaciones en una sola traza.

**Cómo funciona:**
1.  **Inicio de Traza:** En la llamada `POST /api/...`, el Orquestador crea un tramo raíz para el nuevo trabajo.
2.  **Orquestador -> Worker:** En la llamada `actions.dispatch_task(...)`, el `Dispatcher` inyecta automáticamente el Contexto de Rastreo W3C en los encabezados de la solicitud HTTP al worker.
3.  **Worker:** El emulador del worker extrae el contexto de los encabezados y crea un tramo hijo para la duración de la ejecución de la tarea.
4.  **Worker -> Orquestador:** Al enviar el resultado, el worker inyecta su contexto de tramo en los encabezados de la solicitud de devolución de llamada.
5.  **Fin de Traza:** El Orquestador recibe el resultado, extrae el contexto y continúa la traza.

Para ver el resultado, necesitas un colector OpenTelemetry configurado y un backend de visualización (por ejemplo, Jaeger o Zipkin). En la consola, solo verás advertencias sobre el exportador no configurado.

---

### **Recetas para Almacenamiento de Historial**

Esta sección contiene ejemplos de código útiles para la interacción directa con la base de datos necesaria al implementar o extender la funcionalidad de almacenamiento de historial.

#### **Receta 21: Interacción Directa con SQLite**

**Tarea:** Conectarse al archivo de base de datos SQLite, crear tabla e insertar datos usando la librería estándar `sqlite3`.

```python
import sqlite3
import json

# Conectar al archivo DB (creado si no existe)
con = sqlite3.connect("history.db")
cur = con.cursor()

# Crear tabla
cur.execute("""
    CREATE TABLE IF NOT EXISTS job_history (
        event_id TEXT PRIMARY KEY,
        job_id TEXT,
        timestamp TEXT,
        data JSON
    )
""")

# Preparar datos
event_data = {"status": "completed", "result": "ok"}

# Insertar registro usando json.dumps para convertir dict a cadena
cur.execute(
    "INSERT INTO job_history VALUES (?, ?, ?, ?)",
    ("evt_123", "job_abc", "2024-01-01T12:00:00Z", json.dumps(event_data))
)

# Guardar cambios
con.commit()

# Leer datos y convertir cadena JSON de nuevo a dict
res = cur.execute("SELECT job_id, data FROM job_history WHERE event_id = 'evt_123'")
job_id, raw_data = res.fetchone()
retrieved_data = json.loads(raw_data)

print(f"ID Trabajo: {job_id}, Datos: {retrieved_data}")

con.close()
```
*Nota: SQLite soporta nativamente el tipo de datos JSON, simplificando el almacenamiento de estructuras anidadas complejas.*


#### **Receta 22: Interacción Asíncrona con PostgreSQL**

**Tarea:** Conectarse asíncronamente a PostgreSQL, crear tabla e insertar datos usando la librería `asyncpg`.

**Prerrequisito:** `pip install asyncpg`

```python
import asyncio
import asyncpg
import json

async def main():
    # Conectar a PostgreSQL
    conn = await asyncpg.connect(user='user', password='password',
                                 database='db', host='127.0.0.1')

    # Crear tabla usando tipo nativo JSONB para eficiencia
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS job_history (
            event_id TEXT PRIMARY KEY,
            job_id TEXT,
            timestamp TIMESTAMPTZ,
            data JSONB
        )
    """)

    # Preparar datos
    event_data = {"status": "dispatched", "worker": "worker-007"}

    # Insertar registro. asyncpg codifica automáticamente dict a JSONB.
    await conn.execute(
        "INSERT INTO job_history (event_id, job_id, timestamp, data) VALUES ($1, $2, NOW(), $3)",
        "evt_456", "job_xyz", event_data
    )

    # Leer datos. asyncpg decodifica automáticamente JSONB a dict.
    row = await conn.fetchrow(
        "SELECT job_id, data FROM job_history WHERE event_id = $1",
        "evt_456"
    )
    print(f"ID Trabajo: {row['job_id']}, Datos: {row['data']}")

    # Cerrar conexión
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
```
*Nota: Usar `JSONB` en PostgreSQL es preferible sobre `JSON` ya que se almacena en formato binario y permite crear índices sobre claves dentro del documento JSON.*

---

#### **Receta 23: Habilitación y Uso del Historial de Ejecución**

**Tarea:** Habilitar el registro detallado del historial de ejecución de tareas y recuperarlo a través de la API para análisis.

**Paso 1: Habilitar Almacenamiento de Historial**

El historial de ejecución está deshabilitado por defecto. Para habilitarlo, establece la variable de entorno `HISTORY_DATABASE_URI`.

*   **Para SQLite:**
    ```bash
    export HISTORY_DATABASE_URI="sqlite:path/to/your_history.db"
    ```

*   **Para PostgreSQL:**
    ```bash
    export HISTORY_DATABASE_URI="postgresql://user:password@hostname/dbname"
    ```

Después de configurar esta variable, el Orquestador comienza automáticamente a registrar eventos en la base de datos especificada.

**Paso 2: Recuperar Historial vía API**

Cuando el historial está habilitado, un nuevo punto final se vuelve disponible.

*   **Solicitud:**
    ```bash
    curl http://localhost:8080/api/jobs/{job_id}/history -H "X-Client-Token: your_token"
    ```

*   **Ejemplo de Respuesta:**
    ```json
    [
        {
            "event_id": "a1b2c3d4-...".
            "job_id": "job_123",
            "timestamp": "2024-08-27T10:00:00.123Z",
            "state": "start",
            "event_type": "state_started",
            "duration_ms": null,
            "context_snapshot": { "... (estado completo del trabajo al inicio) ..." }
        },
        {
            "event_id": "e5f6g7h8-...".
            "job_id": "job_123",
            "timestamp": "2024-08-27T10:00:01.456Z",
            "state": "start",
            "event_type": "state_finished",
            "duration_ms": 1333,
            "next_state": "processing",
            "context_snapshot": { "... (estado del trabajo después de ejecución del manejador) ..." }
        }
    ]
    ```
Este punto final proporciona una línea de tiempo paso a paso completa de cualquier tarea para un análisis y depuración detallados.

---

### **Receta 24: Autenticación con Token Individual**

**Tarea:** Mejorar la seguridad configurando un token de autenticación único para cada worker en lugar de usar un solo secreto compartido.

**Concepto:**
El sistema admite un modelo de autenticación híbrido. Se da prioridad al token individual vinculado a `worker_id`. Si no se encuentra, el sistema verifica el `WORKER_TOKEN` compartido para compatibilidad con versiones anteriores.

#### **Paso 1: Configuración del Orquestador**
Define tokens individuales en el archivo `workers.toml` en el directorio raíz del Orquestador.

```toml
# workers.toml
[worker-001]
token = "super-secret-token-for-worker-1"
# ... otros metadatos para este worker ...

[worker-002]
token = "another-unique-token-for-worker-2"
```
El Orquestador carga estos tokens en Redis al inicio.

#### **Paso 2: Configuración del Worker**
El Worker debe pasar su ID y token único. Configura las variables de entorno:

```bash
# Identificador único que coincide con la clave en workers.toml
export WORKER_ID="worker-001"
# Token individual para este worker
export WORKER_INDIVIDUAL_TOKEN="super-secret-token-for-worker-1"

# WORKER_TOKEN compartido ya no es necesario si se usa uno individual
```

#### **Paso 3: Lanzamiento**
Ejecuta el Orquestador y el Worker. El Worker se autentica usando `WORKER_ID` y `WORKER_INDIVIDUAL_TOKEN`. Las solicitudes de workers con token inválido o no listados en `workers.toml` (si no se establece `WORKER_TOKEN` compartido) serán rechazadas con `401 Unauthorized`.

```
