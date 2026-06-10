# Arquitectura del Orchestrator

Este documento describe la arquitectura de alto nivel del sistema de orquestación, sus componentes clave y su interacción.

> **Nota:** Este documento describe la **implementación en Python** del estándar HLN. Para la especificación arquitectónica de alto nivel, consulte el paquete `hln`.

**ES** | [EN](../../architecture.md) | [RU](../ru/architecture.md)

## Esquema General

El sistema consta de un **Orchestrator** central y múltiples **Workers**.

### Diagrama de Componentes
(Consulte la versión en inglés para el diagrama Mermaid)

## Principios de Alto Rendimiento

Avtomatika está optimizado para un rendimiento máximo y una latencia mínima:

1.  **Entrada/Salida No Bloqueante (Non-Blocking Everything)**:
    *   **Logging**: Uso de `QueueHandler` para delegar el formateo y la escritura de logs a un hilo secundario.
    *   **Serialización**: Las operaciones pesadas de Msgpack se delegan a un **Thread Pool** mediante `run_in_executor`.
    *   **Webhooks**: Se envían a través de un pool de workers paralelo.

2.  **Protocolos y Algoritmos Estandarizados**:
    *   **Smart Matching Unificado (RXON)**: Uso de la lógica formalizada del protocolo para la selección de workers. Soporte **GE (Greater or Equal)** para cualquier propiedad numérica (VRAM, RAM, CPU).
    *   **Normalización Profunda**: La capa de almacenamiento implementa el desempaquetado recursivo de Msgpack para eliminar artefactos de Redis Lua, garantizando el 100% de integridad de los datos.
    *   **Work Stealing**: Los workers inactivos pueden "robar" tareas de forma atómica de colegas sobrecargados en tiempo O(1).

    3.  **Límite de Tasa Inteligente (Distribuido)**:
    *   **Protección Anti-Spoofing**: Los límites de tasa para los workers están vinculados al hash criptográfico de sus credenciales. Esto hace imposible eludir los límites rotando identificadores de worker.
    *   **Atomicidad**: Uso de Redis para sincronizar los límites entre múltiples instancias del orquestador.

    ## Seguridad (Zero Trust Architecture)

    Avtomatika implementa un modelo de seguridad multicapa:
    *   **Verificación de Cadena de Identidad (Identity Chain)**: Cada señal o evento se verifica a lo largo de toda su ruta de propagación. No solo confiamos en el último remitente, verificamos el origen.
    *   **mTLS (Mutual TLS)**: Autenticación mutua obligatoria entre Orchestrator y Workers mediante certificados.
    *   **STS (Security Token Service)**: Rotación automática de tokens de acceso de corta duración.
    *   **Privacidad de la API**: Control estricto del detalle de las respuestas mediante `DETAILED_API_RESPONSES`. Los secretos y los snapshots de contexto técnico se filtran automáticamente en todos los puntos finales y webhooks.
    *   **Soporte para Firmas**: El motor está preparado para verificar firmas digitales en el `SecurityContext` para asegurar la integridad de las tareas de extremo a extremo.

## Seguridad de Contratos (Contract Enforcement)

Un elemento crítico de protección es la validación estricta de los datos de entrada и salida mediante `output_schema`.

1.  **Validación de Dos Niveles**:
    *   **Worker-side**: El SDK del Worker valida el resultado antes de enviarlo, evitando la transmisión de datos obviamente incorrectos.
    *   **Orchestrator-side**: El núcleo del Orchestrator vuelve a validar los datos antes de guardarlos en `state_history`. Cualquier desviación mueve la tarea al estado `failed` con un código `CONTRACT_VIOLATION`.

2.  **Jerarquía de Esquemas (Schema Priority)**:
    *   **Blueprint Priority**: Si un esquema se define explícitamente en el blueprint (`actions.dispatch_task(..., output_schema=...)`), tiene prioridad absoluta. El Orchestrator ignorará las declaraciones del worker и aplicará la "ley del blueprint".
    *   **Worker Declaration**: Si no se especifica ningún esquema en el blueprint, se utiliza el esquema proporcionado por el worker durante el registro.
    *   **Safe Defaults**: Por defecto, los esquemas se configuran con `additionalProperties: false`, lo que evita la **Inyección de Estado (State Injection)** — un intento de un worker de inyectar campos en la memoria del orquestador que no estaban previstos por la lógica.

## Componentes Clave

### 1. `OrchestratorEngine`
Coordinador central que gestiona el ciclo de vida de los procesos en segundo plano y el enrutamiento de mensajes RXON.

### 2. `Dispatcher`
Router inteligente que empareja los requisitos de las tareas con los recursos de los workers basándose en el estándar del protocolo.

### 3. `StorageBackend`
*   **RedisStorage**: Almacenamiento principal de alto rendimiento para colas de tareas y estados.
*   **HistoryStorage**: Capa de archivo (PostgreSQL/SQLite) para auditoría e historial de eventos.
