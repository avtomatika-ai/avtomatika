[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/observability.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/observability.md)

# Sistema de Observabilidad de Avtomatika

El proyecto implementa un sistema de observación moderno basado en el estándar **OpenTelemetry (OTel)**. Combina el rastreo distribuido y la recolección de métricas en un único flujo de datos, proporcionando una transparencia total del orquestador y los workers.

---

## 1. Principios Core

El sistema se basa en tres pilares:

1.  **Unificación**: Uso del protocolo **OTLP** para transmitir tanto métricas como trazas.
2.  **Contexto de Extremo a Extremo**: Paso de `trace_id` a través de los mensajes del protocolo RXON desde la API hasta el worker final.
3.  **Opcionalidad**: La telemetría no requiere la instalación obligatoria del SDK. Si faltan los paquetes `opentelemetry-*`, el sistema utiliza stubs No-Op ligeros sin afectar el rendimiento.

---

## 2. Rastreo Distribuido

El rastreo le permite seguir la ruta de ejecución completa de una tarea.

### Estructura de Spans

- **`rxon_message:{type}`**: Creado en `engine.py` al recibir cualquier mensaje de un worker (`poll`, `result`, `heartbeat`).
  - _Atributos_: `worker.id_hint`, `message.type`, `auth.worker_id`.
- **`JobExecutor:{blueprint}:{state}`**: Creado durante la ejecución de un paso de lógica en el orquestador.
  - _Atributos_: `job.id`, `job.blueprint`, `job.client_token`, `job.retry_count`.
  - _Eventos_: Los errores se registran a través de `span.record_exception(e)` con un seguimiento de pila completo.

### Propagación de Contexto

El orquestador extrae el contexto de los encabezados de la solicitud de la API HTTP y lo almacena en el campo `tracing_context` del objeto de trabajo. Este contexto se pasa al worker en los metadatos de la tarea, lo que permite combinar el trabajo del orquestador y del worker externo en una sola traza.

---

## 3. Métricas

Las métricas se recopilan en tiempo real y se envían al colector a través del protocolo OTLP.

### Indicadores Clave

| Métrica                                       | Tipo      | Descripción                                                            |
| :-------------------------------------------- | :-------- | :--------------------------------------------------------------------- |
| `orchestrator_jobs_total`                     | Counter   | Número total de trabajos creados (por blueprints).                     |
| `orchestrator_jobs_failed_total`              | Counter   | Número de trabajos fallidos.                                           |
| `orchestrator_job_duration_seconds`           | Histogram | Distribución del tiempo de ejecución del trabajo (P95/P99).            |
| `orchestrator_task_queue_length`              | Gauge     | Número actual de tareas en la cola de Redis.                           |
| `orchestrator_active_workers`                 | Gauge     | Número de workers activos en la red.                                   |
| `orchestrator_loop_lag_seconds`               | Gauge     | Retraso del bucle de eventos asyncio (indicador de sobrecarga de CPU). |
| `orchestrator_ratelimit_blocked_total`        | Counter   | Número de solicitudes bloqueadas por el limitador de tasa.             |
| `orchestrator_jobs_timeouts_total`            | Counter   | Número de trabajos que han expirado por tiempo de espera.              |
| `orchestrator_tasks_ignored_total`            | Counter   | Número de resultados ignorados (tardíos o cancelados).                 |
| `orchestrator_tasks_hot_dispatched_total`     | Counter   | Tareas enviadas a workers HOT (con caché precalentada).                |
| `orchestrator_s3_operations_total`            | Counter   | Número total de operaciones con almacenamiento S3.                     |
| `orchestrator_s3_operation_duration_seconds`  | Histogram | Duración de las operaciones S3.                                        |
| `orchestrator_scheduler_jobs_triggered_total` | Counter   | Número de trabajos activados por el planificador.                      |

### Métricas de Seguridad (Zero Trust)

| Métrica                                         | Tipo    | Descripción                                                        |
| :---------------------------------------------- | :------ | :----------------------------------------------------------------- |
| `orchestrator_security_auth_failures_total`     | Counter | Total de intentos fallidos de autenticación (token/cert inválido). |
| `orchestrator_security_replay_detected_total`   | Counter | Número de ataques de reproducción detectados (timestamp expirado). |
| `orchestrator_security_identity_mismatch_total` | Counter | Desajustes entre el CN del certificado y el worker_id reclamado.   |

---

## 4. Configuración y Lanzamiento

La telemetría se habilita automáticamente si las bibliotecas necesarias y la configuración del entorno están presentes.

### Variables de Entorno

- `OTEL_EXPORTER_OTLP_ENDPOINT`: URL de su OTel Collector o Jaeger (p. ej., `http://localhost:4317`).
- `OTEL_SERVICE_NAME`: Nombre del servicio (por defecto `avtomatika`).
- **Atributos automáticos**: El orquestador añade automáticamente `service.version` (desde los metadatos del paquete) a cada traza y métrica.
- `LOG_LEVEL`: Establezca en `DEBUG` para depurar la exportación de telemetría.

### Ejemplo de Lanzamiento con Jaeger (localmente)

1. Inicie Jaeger a través de Docker:

```bash
docker run -d --name jaeger \
  -e COLLECTOR_OTLP_ENABLED=true \
  -p 16686:16686 \
  -p 4317:4317 \
  jaegertracing/all-in-one:latest
```

2. Inicie el orquestador con el endpoint especificado:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"
pip install "avtomatika[telemetry]"
python -m avtomatika.engine
```

Ahora todas las trazas estarán disponibles en la interfaz de Jaeger en `http://localhost:16686`.

---

## 5. Uso en Código (para desarrolladores)

Si la lógica de su blueprint contiene cálculos complejos, trabajo con bases de datos externas o APIs, puede detallar el rastreo creando sub-spans personalizados.

### ¿Por qué es esto importante?

El span automático `JobExecutor` muestra el tiempo total de ejecución de un paso. Los spans personalizados permiten ver cuánto tiempo tomó una operación específica (por ejemplo, `Data:Fetch` o `LLM:Preprocessing`), lo cual es crítico para optimizar los pipelines.

### Ejemplo en un Manejador de Blueprint:

```python
from avtomatika.telemetry import trace

tracer = trace.get_tracer("my_blueprint")

@bp.handler
async def process_data(initial_data, actions):
    # 1. Crear un span personalizado dentro del paso
    with tracer.start_as_current_span("Internal:Transform") as span:
        span.set_attribute("data.size", len(initial_data))
        # ... su lógica pesada de transformación ...
        result = {"status": "ok"}

    # 2. La lógica principal del orquestador continúa
    actions.go_to("next_step")
```

### Ejemplo de adición de una métrica:

```python
from avtomatika import metrics

# En el método de inicialización
meter = metrics.get_meter("avtomatika")
my_counter = meter.create_counter("custom_event_total")

# En el código
my_counter.add(1, {"type": "alert"})
```

---

_Nota: Si `OTEL_EXPORTER_OTLP_ENDPOINT` no está configurado, los datos se enviarán a la consola (ConsoleExporter), lo cual es conveniente para verificar el funcionamiento correcto sin desplegar Jaeger._
