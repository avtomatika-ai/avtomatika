[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/cookbook/advanced_topics.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/cookbook/advanced_topics.md)

# Recetario: Características Avanzadas

Esta sección describe características del sistema más complejas pero potentes, útiles en escenarios del mundo real.

## 1. Conectar Worker a Múltiples Orquestadores

Para asegurar alta disponibilidad y distribución de carga, el SDK del Worker puede conectarse a múltiples instancias del Orquestador.

-   **Variable de Entorno:** `ORCHESTRATORS_CONFIG` (reemplaza `ORCHESTRATOR_URL`). Contiene cadena JSON con lista de direcciones.
-   **Modo de Operación:** `MULTI_ORCHESTRATOR_MODE`.

### Modo `FAILOVER` (Alta Disponibilidad)

Este es el modo predeterminado. El Worker trabajará con el primer Orquestador de la lista. Si deja de estar disponible, el Worker cambia automáticamente al siguiente.

**Configuración de Ejemplo:**
```dotenv
# El worker sondeará 'main-orchestrator'. Si falla,
# el SDK cambia automáticamente a 'backup-orchestrator'.
ORCHESTRATORS_CONFIG='[
    {"url": "http://main-orchestrator:8080"},
    {"url": "http://backup-orchestrator:8080"}
]'

MULTI_ORCHESTRATOR_MODE=FAILOVER
```

### Modo `ROUND_ROBIN` (Equilibrio de Carga)

En este modo, el Worker enviará secuencialmente solicitudes de tareas a cada Orquestador de la lista, distribuyendo la carga.

**Configuración de Ejemplo:**
```dotenv
ORCHESTRATORS_CONFIG='[
    {"url": "http://orchestrator-1:8080"},
    {"url": "http://orchestrator-2:8080"}
]'

MULTI_ORCHESTRATOR_MODE=ROUND_ROBIN
```

## 2. Autenticación Avanzada de Worker (`workers.toml`)

En lugar de usar un solo `WORKER_TOKEN` compartido para todos los workers, puedes asignar un token individual a cada uno. Esto es más seguro ya que permite la revocación de acceso granular.

**1. Crear archivo `workers.toml`** en la raíz del Orquestador:
```toml
# workers.toml
[worker-video-1]
token = "unique-secret-token-for-video-1"

[worker-audio-5]
token = "another-secret-for-audio-5"
```

**2. Especificar ruta al archivo** en la configuración del Orquestador:
```dotenv
# En archivo .env del Orquestador
WORKERS_CONFIG_PATH="workers.toml"
```

**3. Configurar Worker** para usar su ID y token individual:
```dotenv
# En archivo .env del Worker
WORKER_ID="worker-video-1"
AVTOMATIKA_WORKER_TOKEN="unique-secret-token-for-video-1"
```
El Orquestador siempre intentará autenticar primero al worker por su token individual.

## 3. Manejo de Archivos Grandes (Descarga de Carga Útil S3)

El SDK del Worker soporta el manejo de archivos grandes a través de almacenamiento compatible con S3 de forma nativa, evitando la sobrecarga de Redis.

-   **Descarga Automática:** Si los parámetros de la tarea contienen un URI como `s3://...`, el SDK descarga automáticamente el archivo y reemplaza el URI con la ruta local antes de llamar a tu manejador.
-   **Carga Automática:** Si tu manejador devuelve una ruta de archivo local, el SDK la sube automáticamente a S3 y reemplaza la ruta con el nuevo URI `s3://` en el resultado final.

Para habilitarlo, simplemente configura las variables de entorno en el worker:
```dotenv
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=worker-results
WORKER_PAYLOAD_DIR=/tmp/payloads # Directorio permitido para carga
```

## 4. Consejos de Depuración

### Verificación del Estado e Historial de la Tarea

Usa `curl` para solicitar el estado y el historial de ejecución de una tarea. Ayuda a entender el paso del proceso y el contenido de los datos.

```bash
# Especifica tu token de cliente
TOKEN="your-secret-orchestrator-token"

# Obtener estado actual de la tarea
curl -H "X-Client-Token: $TOKEN" http://localhost:8080/api/v1/jobs/{job_id}

# Obtener historial completo de eventos de la tarea (muy útil para depuración)
curl -H "X-Client-Token: $TOKEN" http://localhost:8080/api/v1/jobs/{job_id}/history
```

### Visualización de la Lógica del Blueprint

Para entender la lógica compleja de un pipeline, puedes obtener su grafo en formato DOT y visualizarlo.

**1. Obtener representación gráfica DOT:**
```bash
curl -H "X-Client-Token: $TOKEN" http://localhost:8080/api/v1/blueprints/{blueprint_name}/graph
```

**2. Visualizar resultado:**
Copia la salida de texto recibida y pégala en un visualizador en línea, por ejemplo, [Graphviz Online](https://dreampuf.github.io/GraphvizOnline/). Verás inmediatamente el diagrama de estado y transición de tu blueprint.
