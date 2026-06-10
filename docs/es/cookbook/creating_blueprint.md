[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/creating_blueprint.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/cookbook/creating_blueprint.md)

# Recetario: Creación de un Blueprint (Pipeline)

Los Blueprints (`Blueprint`) son la base para definir la lógica comercial en el sistema. Cada blueprint representa una máquina de estados ("guion") que el Orquestador ejecutará.

Esta guía mostrará cómo crear un pipeline simple pero completo.

## Paso 1: Crear un Archivo Blueprint

Se recomienda almacenar los blueprints en un archivo separado, por ejemplo, `mi_servicio/blueprints.py`.

## Paso 2: Definir el Blueprint

Importar `Blueprint` y crear una instancia.

- `name`: Nombre único del blueprint.
- `api_version`: Versión de la API (por ejemplo, "v1").
- `api_endpoint`: URL donde los clientes crearán tareas para este pipeline.

```python
from avtomatika import Blueprint

# Crear instancia de blueprint
order_pipeline = Blueprint(
    name="order_processing_flow",
    api_version="v1",
    api_endpoint="/jobs/process_order"  # URL para creación de tareas
)
```

## Paso 3: Definir Estados и Manejadores

Cada paso en tu proceso es un "estado" con una función "manejadora" adjunta.

-   El decorador `@blueprint.handler` vincula la función al estado.
-   **Estado Inicial** debe haber exactamente uno, marcado con `is_start=True`.
-   **Estados Finales** pueden ser múltiples, marcados con `is_end=True`.

El manejador recibe argumentos a través de **Inyección de Dependencias**. Puede solicitar:
- `initial_data`: Datos originales del trabajo.
- `actions`: Objeto `ActionFactory` para controlar el proceso.
- `state_history`: Historial completo del trabajo.
- `job_id`: ID único del trabajo.

```python
from avtomatika import ActionFactory

@order_pipeline.handler(is_start=True)
async def start(initial_data: dict, actions: ActionFactory, job_id: str):
    """
    Manejador inicial. Llamado cuando se crea el Job.
    """
    print(f"Trabajo {job_id}: procesamiento de pedido iniciado.")
    print(f"Datos de entrada: {initial_data}")

    # Transición al siguiente paso
    actions.go_to("dispatch_to_worker")


@order_pipeline.handler
async def dispatch_to_worker(initial_data: dict, actions: ActionFactory):
    """
    Este manejador despacha la tarea al worker.
    """
    # Pausar pipeline y esperar resultado del worker
    actions.dispatch_task(
        task_type="check_inventory",  # Tipo de tarea que entiende el worker
        params={"items": initial_data.get("items")},
        
        # Definir a dónde va el proceso dependiendo de la respuesta del worker
        transitions={
            "success": "inventory_ok",      # Si el worker devuelve status="success"
            "out_of_stock": "inventory_failed", # Si el worker devuelve estado personalizado
            "failure": "generic_failure"    # Si el worker devuelve status="failure"
        }
    )

@order_pipeline.handler
async def inventory_ok(actions: ActionFactory, state_history: dict):
    """
    Llamado si el worker confirmó la disponibilidad de artículos.
    """
    # Los datos devueltos por el worker están disponibles en el historial
    print("Artículos disponibles. Finalizando con éxito.")
    actions.go_to("finished_successfully")


@order_pipeline.handler(is_end=True)
async def inventory_failed(actions: ActionFactory):
    """
    Estado final si los artículos están agotados.
    """
    print("No se puede procesar el pedido, artículos agotados.")


@order_pipeline.handler(is_end=True)
async def generic_failure(actions: ActionFactory):
    """
    Estado final para fallos genéricos.
    """
    print("Ocurrió un error de procesamiento.")


@order_pipeline.handler(is_end=True)
async def finished_successfully(actions: ActionFactory):
    """
    Estado final en caso de éxito.
    """
    print("¡Pedido procesado exitosamente!")

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

# Registrar blueprint в motor (¡Activa la validación!)
engine.register_blueprint(order_pipeline)

# Ejecutar motor
engine.run()
```

Después de esto, puedes crear tareas enviando solicitudes POST a `/api/v1/jobs/process_order`.
