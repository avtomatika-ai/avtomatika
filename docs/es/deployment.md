[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/deployment.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/deployment.md)

# Despliegue y Pruebas

Este documento contiene recomendaciones para ejecutar el sistema en un entorno de producción, así como instrucciones para el desarrollo y las pruebas locales.

## Despliegue de Producción

### Opción 1: Gunicorn (Recomendado)

```bash
gunicorn app:app --bind 0.0.0.0:8080 --worker-class aiohttp.GunicornWebWorker
```

### Opción 2: Uvicorn

```bash
uvicorn app:app --host 0.0.0.0 --port 8080
```

## Desarrollo Local

### Ejecución de todo el sistema (Docker Compose)

```bash
docker-compose up --build
```

-   **Orchestrator API** estará disponible en `http://localhost:8080`.

### Ejecución de Pruebas

Es **crucial** ejecutar las pruebas después de realizar cambios.

**1. Configurar el entorno:**
```bash
pip install -e ".[all,test]"
```

**2. Ejecutar pruebas:**
```bash
pytest tests/
```

## Documentación Adicional

- [**Guía de Arquitectura**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/architecture.md)
- [**Referencia de API**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/api_reference.md)
- [**Guía de Configuración**](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/es/configuration.md)
