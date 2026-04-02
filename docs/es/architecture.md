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
    *   **Smart Matching Unificado (RXON v1.0b7)**: Uso de la lógica formalizada del protocolo para la selección de workers. Soporte **GE (Greater or Equal)** para cualquier propiedad numérica (VRAM, RAM, CPU).
    *   **Normalización Profunda (Beta 20 Fix)**: La capa de almacenamiento implementa el desempaquetado recursivo de Msgpack para eliminar artefactos de Redis Lua, garantizando el 100% de integridad de los datos.
    *   **Work Stealing**: Los workers inactivos pueden "robar" tareas de forma atómica de colegas sobrecargados en tiempo O(1).

## Seguridad (Zero Trust Architecture)

Avtomatika implementa un modelo de seguridad multicapa:
*   **Verificación de Cadena de Identidad (Identity Chain)**: Cada señal o evento se verifica a lo largo de toda su ruta de propagación. No solo confiamos en el último remitente, verificamos el origen.
*   **mTLS (Mutual TLS)**: Autenticación mutua obligatoria entre Orchestrator y Workers mediante certificados.
*   STS (Security Token Service): Rotación automática de tokens de acceso de corta duración.
*   **Soporte para Firmas**: El motor está preparado para verificar firmas digitales en el `SecurityContext` para asegurar la integridad de las tareas de extremo a extremo.

## Componentes Clave

### 1. `OrchestratorEngine`
Coordinador central que gestiona el ciclo de vida de los procesos en segundo plano y el enrutamiento de mensajes RXON.

### 2. `Dispatcher`
Router inteligente que empareja los requisitos de las tareas con los recursos de los workers basándose en el estándar del protocolo.

### 3. `StorageBackend`
*   **RedisStorage**: Almacenamiento principal de alto rendimiento para colas de tareas y estados.
*   **HistoryStorage**: Capa de archivo (PostgreSQL/SQLite) para auditoría e historial de eventos.

---
*Documentación actualizada para la versión v1.0b7.*
