[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/observability_protocol.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/observability_protocol.md)

# Protocolo de Rastreo Distribuido (Orquestador ↔ Worker)

Este documento describe la lógica de propagación de telemetría de extremo a extremo entre el Orquestador y los Workers utilizando el protocolo RXON y los estándares de OpenTelemetry.

---

## 1. Flujo de Trabajo de Interacción

La secuencia garantiza que un único "hilo de rastreo" siga la tarea desde la llamada inicial a la API hasta la ejecución real en un worker remoto y de regreso.

### Paso 1: Despacho de Tarea (Orquestador)

Cuando el `JobExecutor` decide enviar una tarea a un worker:

1.  Crea un span hijo para la operación de despacho.
2.  Utiliza el `TextMapPropagator` de OpenTelemetry para "inyectar" el contexto del span actual en un diccionario.
3.  Este diccionario se almacena en el campo `tracing_context` de la carga útil de la tarea.
4.  **Opcionalidad**: Si OTel está desactivado en el Orquestador, `tracing_context` estará vacío o ausente.

### Paso 2: Recepción de Tarea (Worker)

Cuando el Worker recibe una tarea a través de la respuesta `poll`:

1.  Verifica la clave `tracing_context` en los datos de la tarea.
2.  **Lógica**:
    - **Si está presente Y OTel está habilitado en el Worker**: El Worker "extrae" el contexto y comienza su propio span como un hijo del span del Orquestador.
    - **Si está ausente O OTel está deshabilitado en el Worker**: El Worker ejecuta la tarea normalmente sin ninguna sobrecarga de telemetría.

### Paso 3: Envío de Resultados (Worker)

Una vez finalizada la ejecución:

1.  **Si OTel está habilitado en el Worker**: El Worker inyecta su contexto de span actual en el campo `tracing_context` del mensaje `result`.
2.  **Si OTel está deshabilitado**: El mensaje `result` se envía sin metadatos de rastreo.

### Paso 4: Procesamiento de Resultados (Orquestador)

Cuando el Orquestador recibe el mensaje `result`:

1.  Verifica el `tracing_context` en la carga útil del resultado.
2.  **Lógica**:
    - **Si está presente**: El Orquestador vincula el contexto recibido a su propio span de "Procesamiento de Resultados". Esto crea un "salto" visual en Jaeger entre los dos sistemas.
    - **Si está ausente**: El Orquestador procede con su propio rastreo interno. No se generan errores ni advertencias.

---

## 2. Mapeo del Protocolo RXON

El `tracing_context` es un campo estándar en la raíz de los objetos de tarea y resultado de RXON.

**Ejemplo de Carga Útil de Tarea (Orquestador → Worker):**

```json
{
  "task_id": "...",
  "type": "inference",
  "tracing_context": {
    "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
  },
  "params": { ... }
}
```

**Ejemplo de Carga Útil de Resultado (Worker → Orquestador):**

```json
{
  "job_id": "...",
  "status": "success",
  "tracing_context": {
    "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-af34902b70f067aa-01"
  },
  "data": { ... }
}
```

---

## 3. Degradación Grácil y Estabilidad

El sistema está diseñado pensando en **Zero-Friction**:

1.  **Fallback sin dependencias**: El Orquestador utiliza stubs No-Op. Si un worker envía un contexto de rastreo, pero el Orquestador no tiene instalado el SDK de OTel, el contexto simplemente se ignora a nivel de lógica sin ningún coste de rendimiento.
2.  **Versionado del Protocolo**: El campo `tracing_context` es ignorado por los workers más antiguos que no lo soportan (comportamiento estándar de JSON).
3.  **Seguridad**: El `tracing_context` se trata como metadatos y no se utiliza para ninguna lógica de autorización. Si un worker malicioso envía un contexto falso, solo afecta la visualización en Jaeger, no la seguridad del sistema.

---

## 4. Lista de Verificación para SDKs de Worker

Para soportar la observabilidad completa, un SDK de Worker debería:

1.  [ ] Verificar el `tracing_context` en las tareas recibidas.
2.  [ ] Si OTel está disponible, usar `extract(tracing_context)` para establecer el span padre.
3.  [ ] Crear un span llamado `Worker:Execute:{task_type}`.
4.  [ ] Antes de enviar el resultado, llamar a `inject(result_payload['tracing_context'])`.
5.  [ ] Asegurarse de que todas las llamadas a OTel estén envueltas en `try/except` para evitar caídas del worker si la librería OTel se comporta de forma inesperada.
