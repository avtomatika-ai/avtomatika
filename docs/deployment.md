**EN** | [ES](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/deployment.md) | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/deployment.md)

# Deployment and Testing

This document contains recommendations for running the system in a production environment, as well as instructions for local development and testing.

## Production Deployment

The built-in `aiohttp` web server is great for development, but a more robust solution is required for a production environment. Using a production-ready ASGI server is recommended.

### Example 1: Gunicorn (Recommended, time-tested)

Gunicorn is a mature and widely used server with powerful process management capabilities. A special `worker-class` is used to run an `aiohttp` application with it.

```bash
# You will need to create an app.py file that initializes and returns
# the application object from the engine (engine.app)
gunicorn app:app --bind 0.0.0.0:8080 --worker-class aiohttp.GunicornWebWorker
```

### Example 2: Uvicorn (Modern ASGI server)

Uvicorn is a modern, high-performance ASGI server built on `uvloop`. It is also an excellent choice for running `aiohttp` applications.

```bash
# Install uvicorn: pip install uvicorn
uvicorn app:app --host 0.0.0.0 --port 8080
```

**The choice of server depends on your preferences and infrastructure requirements.** Gunicorn provides more advanced process management out of the box, while Uvicorn often shows higher performance in benchmarks for purely asynchronous applications. Both options are reliable for production use.

### Security

**Critically Important:** Do not run the system in production with default tokens. Set unique and secure tokens for the following environment variables:
- `CLIENT_TOKEN` (used to verify `X-Client-Token` header from clients)
- `GLOBAL_WORKER_TOKEN` (used to verify `X-Worker-Token` header from workers, if individual token is not set)

For S3-enabled deployments, ensure that `S3_ACCESS_KEY` and `S3_SECRET_KEY` are stored securely (e.g., using Docker Secrets or Vault).

For enhanced security, use individual tokens for each worker using the `workers.toml` file.

## Local Development and Testing

### Running the Entire System (via Docker Compose)

The easiest way to run all components (Orchestrator, UI, Redis, Worker) is to use Docker Compose.

```bash
docker-compose up --build
```

-   **Orchestrator API** will be available at `http://localhost:8080`.
-   **Control Panel (UI)** (if present in `docker-compose.yml`) will be available at `http://localhost:8082`.

### Running Tests

There are several test suites in the project. It is **crucial** to run them after making changes.

**1. Setup Test Environment:**
Install all necessary dependencies, including test ones.
```bash
pip install -e ".[all,test]"
```

**2. Orchestrator Core Tests:**
```bash
pytest avtomatika/tests/
```

**3. Worker SDK Tests:**
```bash
pytest avtomatika-worker/tests/
```

**4. E2E Tests (if applicable):**
If the project has end-to-end tests using Playwright, running them might require pre-installing browsers.

```bash
# Install browsers for Playwright (if not installed)
playwright install

# Run E2E tests (require system running via docker-compose up)
pytest tests/e2e/
```