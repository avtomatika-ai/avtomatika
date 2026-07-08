[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/creating_worker.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/cookbook/creating_worker.md)

# Recetario: Creación de un Worker

Los Workers son ejecutores independientes que realizan el trabajo real. Esta guía muestra cómo crear un worker utilizando el SDK `avtomatika-worker`.

## Paso 1: Instalar SDK

Puede instalar la versión base o una versión con capacidades adicionales:

```bash
# Versión mínima (sin S3 ni Pydantic)
pip install avtomatika-worker

# Versión completa (recomendada para auto-validación y manejo de archivos)
pip install "avtomatika-worker[s3,pydantic]"
```

## Paso 2: Crear Archivo de Worker

Crea un archivo Python (por ejemplo, `mi_worker.py`) e importa la clase `Worker`.

# 2. Definir Manejadores de Tareas

Puede usar **diccionarios estándar** (sin dependencias) o **modelos Pydantic** (para validación automática).

### Opción A: Diccionario Simple (Sin Pydantic)

```python
@worker.skill("check_inventory")
async def check_inventory_handler(params: dict, **kwargs) -> dict:
    items = params.get("items", [])
    print(f"Verificando artículos: {items}")
    return {"status": "success", "data": {"warehouse_info": "ok"}}
```

### Opción B: Modelo Pydantic (Validación automática)

```python
from pydantic import BaseModel

class CheckParams(BaseModel):
    items: list[str]

@worker.skill("check_inventory")
async def check_inventory_handler(params: CheckParams, **kwargs) -> dict:
    """El SDK validará automáticamente los datos entrantes contra el modelo CheckParams."""
    print(f"Verificando artículos: {params.items}")
    return {"status": "success", "data": {"warehouse_info": "ok"}}
```

# Ejemplo de manejador para tarea larga con cancelación cooperativa

@worker.skill("long_running_task")
async def long_task_handler(params: dict, \*\*kwargs) -> dict:
task_id = kwargs["task_id"]
print(f"Iniciando tarea larga {task_id}...")

    for i in range(10):
        # Verificar si el Orquestador solicitó la cancelación
        if await worker.check_for_cancellation(task_id):
            print(f"Cancelación detectada para la tarea {task_id}. Deteniendo...")
            return {"status": "cancelled", "message": "La tarea fue cancelada por el usuario."}

        print(f"Paso {i+1}/10 hecho...")
        await asyncio.sleep(2)

    return {"status": "success"}

# 4. Ejecutar worker

if **name** == "**main**":
worker.run()

````

## Paso 3: Configuración de Conexión y Autenticación

Crea un archivo `.env` en el mismo directorio que `mi_worker.py` o exporta las variables al entorno.

```dotenv
# ID único de esta instancia de worker
WORKER_ID=inventory-worker-01

# Dirección de tu Orquestador
ORCHESTRATOR_URL=http://localhost:8080

# Token para autenticación del worker. Debe coincidir con el token esperado por el Orquestador
# (global o individual).
WORKER_TOKEN=tu-token-secreto-de-worker

# (Opcional) Habilitar WebSocket para cancelación instantánea de tareas
WORKER_ENABLE_WEBSOCKETS=true
````

## Paso 4: Lanzamiento

Simplemente ejecuta tu archivo Python:

```bash
python mi_worker.py
```

El Worker se conectará automáticamente al Orquestador, se registrará y comenzará a sondear nuevas tareas.

## Mecanismos de Cancelación

El SDK proporciona dos mecanismos de cancelación:

1.  **WebSocket (Modelo Push):** Si `WORKER_ENABLE_WEBSOCKETS=true`, el Orquestador puede enviar un comando de cancelación inmediato. Esto genera `asyncio.CancelledError` en tu manejador. Esto proporciona la reacción más rápida, solo envuelve tu código en `try...except asyncio.CancelledError` para limpieza si es necesario.

2.  **Redis (Modelo Pull):** Incluso sin WebSocket, puedes implementar cancelación "cooperativa" para tareas muy largas. El SDK proporciona la función asíncrona `worker.check_for_cancellation(task_id)`. Llámala periódicamente dentro de tu bucle de procesamiento. Si devuelve `True`, el Orquestador solicitó la cancelación. Tu código debe interrumpir con elegancia y devolver el estado `cancelled`. (Ver ejemplo `long_running_task` arriba).
