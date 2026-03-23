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

## Local Development and Testing

### Running the Entire System (via Docker Compose)

The easiest way to run all components (Orchestrator, UI, Redis, Worker) is to use Docker Compose.

```bash
docker-compose up --build
```

-   **Orchestrator API** will be available at `http://localhost:8080`.

### Running Tests

It is **crucial** to run tests after making changes.

**1. Setup Test Environment:**
```bash
pip install -e ".[all,test]"
```

**2. Run Core Tests:**
```bash
pytest tests/
```

## Detailed Documentation

- [**Architecture Guide**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/architecture.md)
- [**API Reference**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/api_reference.md)
- [**Configuration Guide**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/configuration.md)
