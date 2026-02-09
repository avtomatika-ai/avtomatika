[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/creating_blueprint.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/cookbook/creating_blueprint.md)

# Recetario: Creación de un Blueprint (Pipeline)

Los Blueprints (`StateMachineBlueprint`) son la base para definir la lógica comercial en el sistema. Cada blueprint representa una máquina de estados ("guion") que el Orquestador ejecutará.

Esta guía mostrará cómo crear un pipeline simple pero completo.

## Paso 1: Crear un Archivo Blueprint

Se recomienda almacenar los blueprints en un archivo separado, por ejemplo, `mi_servicio/blueprints.py`.

## Paso 2: Definir el Blueprint

Importar `StateMachineBlueprint` y crear una instancia.

- `name`: Nombre único del blueprint.
- `api_version`: Versión de la API (por ejemplo, "v1").
- `api_endpoint`: URL donde los clientes crearán tareas para este pipeline.

```python
from avtomatika import StateMachineBlueprint

# Crear instancia de blueprint
order_pipeline = StateMachineBlueprint(
    name="order_processing_flow",
    api_version="v1",
    api_endpoint="/jobs/process_order"  # URL para creación de tareas
)
```

## Paso 3: Definir Estados y Manejadores

Cada paso en tu proceso es un "estado" con una función "manejadora" adjunta.

-   El decorador `@blueprint.handler_for("nombre_estado")` vincula la función al estado.
-   **Estado Inicial** debe haber exactamente uno, marcado con `is_start=True`.
-   **Estados Finales** pueden ser múltiples, marcados con `is_end=True`.

El manejador recibe un argumento — `context`, que contiene toda la información de la tarea y métodos de control del proceso (`context.actions`).

```python
@order_pipeline.handler_for("start", is_start=True)
async def start_handler(context):
    """
    Manejador inicial. Llamado cuando se crea el Job.
    """
    print(f"Trabajo {context.job_id}: procesamiento de pedido iniciado.")
    print(f"Datos de entrada: {context.initial_data}")

    # Guardar algo en el historial para los siguientes pasos
    context.state_history["processed_by"] = "start_handler"

    # Transición al siguiente paso
    context.actions.transition_to("dispatch_to_worker")


@order_pipeline.handler_for("dispatch_to_worker")
async def dispatch_handler(context):
    """
    Este manejador despacha la tarea al worker.
    """
    print(f"Trabajo {context.job_id}: despachando tarea 'check_inventory' al worker.")

    # Pausar pipeline y esperar resultado del worker
    context.actions.dispatch_task(
        task_type="check_inventory",  # Tipo de tarea que entiende el worker
        params={"items": context.initial_data.get("items")},
        
        # Definir a dónde va el proceso dependiendo de la respuesta del worker
        transitions={
            "success": "inventory_ok",      # Si el worker devuelve status="success"
            "out_of_stock": "inventory_failed", # Si el worker devuelve estado personalizado
            "failure": "generic_failure"    # Si el worker devuelve status="failure"
        }
    )

@order_pipeline.handler_for("inventory_ok")
async def inventory_ok_handler(context):
    """
    Llamado si el worker confirmó la disponibilidad de artículos.
    """
    # Los datos devueltos por el worker están disponibles en state_history
    worker_data = context.state_history.get("warehouse_info")
    print(f"Trabajo {context.job_id}: artículos en stock. Info del worker: {worker_data}")
    
    context.actions.transition_to("finished_successfully")


@order_pipeline.handler_for("inventory_failed", is_end=True)
async def inventory_failed_handler(context):
    """
    Estado final si los artículos están agotados.
    """
    print(f"Trabajo {context.job_id}: no se puede procesar el pedido, artículos agotados.")


@order_pipeline.handler_for("generic_failure", is_end=True)
async def failed_handler(context):
    """
    Estado final para fallos genéricos.
    """
    print(f"Trabajo {context.job_id}: ocurrió un error de procesamiento.")


@order_pipeline.handler_for("finished_successfully", is_end=True)
async def finished_handler(context):
    """
    Estado final en caso de éxito.
    """
    print(f"Trabajo {context.job_id}: pedido procesado exitosamente!")

```

## Paso 4: Registrar el Blueprint

En el archivo principal de tu aplicación donde ejecutas `OrchestratorEngine`, registra el blueprint creado.

Cuando llamas a `register_blueprint()`, el motor realiza automáticamente una **verificación de integridad**. Asegura que:
1.  El blueprint tiene exactamente un estado inicial.
2.  Todas las transiciones conducen a estados existentes (sin transiciones "colgantes").
3.  Todos los estados son alcanzables desde el estado inicial (sin "código muerto").

Si alguna de estas verificaciones falla, se generará un `ValueError`, evitando que la aplicación se inicie con una configuración rota.

```python
# main.py
from avtomatika import OrchestratorEngine
from mi_servicio.blueprints import order_pipeline  # Importar nuestro blueprint

# ... (configuración de almacenamiento y config)

engine = OrchestratorEngine(storage, config)

# Registrar blueprint en el motor (¡Activa la validación!)
engine.register_blueprint(order_pipeline)

# Ejecutar motor
engine.run()
```

Después de esto, puedes crear tareas enviando solicitudes POST a `/api/v1/jobs/process_order`.
