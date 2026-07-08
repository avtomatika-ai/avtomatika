**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/observability_protocol.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/observability_protocol.md)

# Distributed Tracing Protocol (Orchestrator ↔ Worker)

This document describes the end-to-end telemetry propagation logic between the Orchestrator and Workers using the RXON protocol and OpenTelemetry standards.

---

## 1. Interaction Workflow

The sequence ensures that a single "trace thread" follows the task from the initial API call to the actual execution on a remote worker and back.

### Step 1: Task Dispatch (Orchestrator)

When the `JobExecutor` decides to send a task to a worker:

1.  It creates a child span for the dispatch operation.
2.  It uses the OpenTelemetry `TextMapPropagator` to "inject" the current span context into a dictionary.
3.  This dictionary is stored in the `tracing_context` field of the task payload.
4.  **Optionality**: If OTel is disabled in the Orchestrator, `tracing_context` will be empty.

### Step 2: Task Receipt (Worker)

When the Worker receives a task via the `poll` response:

1.  It checks for the `tracing_context` key in the task data.
2.  **Logic**:
    - **If present AND OTel is enabled on Worker**: Worker "extracts" the context and starts its own span as a child of the Orchestrator's span.
    - **If absent OR OTel is disabled on Worker**: Worker executes the task normally without any telemetry overhead.

### Step 3: Result Submission (Worker)

Once execution is finished:

1.  **If OTel is enabled on Worker**: Worker injects its current span context into the `tracing_context` field of the `result` message.
2.  **If OTel is disabled**: The `result` message is sent without any tracing metadata.

### Step 4: Result Processing (Orchestrator)

When the Orchestrator receives the `result` message:

1.  It checks for `tracing_context` in the result payload.
2.  **Logic**:
    - **If present**: Orchestrator links the received context to its own "Result Processing" span. This creates a visual "jump" in Jaeger between the two systems.
    - **If absent**: Orchestrator proceeds with its own internal tracing. No errors or warnings are generated.

---

## 2. RXON Protocol Mapping

The `tracing_context` is a standard field at the root of RXON task and result objects.

**Example Task Payload (Orchestrator → Worker):**

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

**Example Result Payload (Worker → Orchestrator):**

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

## 3. Graceful Degradation & Stability

The system is designed with **Zero-Friction** in mind:

1.  **No-Dependency Fallback**: The Orchestrator uses No-Op stubs. If a worker sends a trace context, but the Orchestrator doesn't have OTel SDK installed, the context is simply ignored at the logic level without any performance cost.
2.  **Protocol Versioning**: The `tracing_context` field is ignored by older workers that do not support it (standard JSON behavior).
3.  **Security**: The `tracing_context` is treated as metadata and is not used for any authorization logic. If a malicious worker sends a fake context, it only affects the visualization in Jaeger, not the system's security.

---

## 4. Implementation Checklist for Worker SDKs

To support full observability, a Worker SDK should:

1.  [ ] Check for `tracing_context` in received tasks.
2.  [ ] If OTel is available, use `extract(tracing_context)` to set the parent span.
3.  [ ] Create a span named `Worker:Execute:{task_type}`.
4.  [ ] Before sending the result, call `inject(result_payload['tracing_context'])`.
5.  [ ] Ensure all OTel calls are wrapped in `try/except` or use a "soft import" pattern to prevent worker crashes if the OTel library behaves unexpectedly.
