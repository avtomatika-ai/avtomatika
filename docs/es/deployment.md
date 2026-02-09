[EN](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/deployment.md) | **ES** | [RU](https://github.com/avtomatika-ai/avtomatika/blob/main/docs/ru/deployment.md)

# Despliegue y Pruebas

Este documento contiene recomendaciones para ejecutar el sistema en un entorno de producción, así como instrucciones para el desarrollo local y pruebas.

## Despliegue en Producción

El servidor web integrado `aiohttp` es excelente para el desarrollo, pero se requiere una solución más robusta para un entorno de producción. Se recomienda usar un servidor ASGI listo para producción.

### Ejemplo 1: Gunicorn (Recomendado, probado con el tiempo)

Gunicorn es un servidor maduro y ampliamente utilizado con potentes capacidades de gestión de procesos. Se utiliza una `worker-class` especial para ejecutar una aplicación `aiohttp` con él.

```bash
# Necesitarás crear un archivo app.py que inicialice y devuelva
# el objeto de aplicación del motor (engine.app)
gunicorn app:app --bind 0.0.0.0:8080 --worker-class aiohttp.GunicornWebWorker
```

### Ejemplo 2: Uvicorn (Servidor ASGI moderno)

Uvicorn es un servidor ASGI moderno y de alto rendimiento construido sobre `uvloop`. También es una excelente opción para ejecutar aplicaciones `aiohttp`.

```bash
# Instalar uvicorn: pip install uvicorn
uvicorn app:app --host 0.0.0.0 --port 8080
```

**La elección del servidor depende de tus preferencias y requisitos de infraestructura.** Gunicorn proporciona una gestión de procesos más avanzada desde el primer momento, mientras que Uvicorn a menudo muestra un rendimiento más alto en pruebas comparativas para aplicaciones puramente asíncronas. Ambas opciones son fiables para uso en producción.

### Seguridad

**Críticamente Importante:** No ejecutes el sistema en producción con tokens predeterminados. Establece tokens únicos y seguros para las siguientes variables de entorno:
- `CLIENT_TOKEN` (usado para verificar el encabezado `X-Client-Token` de clientes)
- `GLOBAL_WORKER_TOKEN` (usado para verificar el encabezado `X-Worker-Token` de workers, si no se establece un token individual)

Para despliegues con S3 habilitado, asegúrate de que `S3_ACCESS_KEY` y `S3_SECRET_KEY` se almacenen de forma segura (por ejemplo, usando Docker Secrets o Vault).

Para mayor seguridad, usa tokens individuales para cada worker utilizando el archivo `workers.toml`.

## Desarrollo Local y Pruebas

### Ejecutar Todo el Sistema (vía Docker Compose)

La forma más fácil de ejecutar todos los componentes (Orquestador, UI, Redis, Worker) es usar Docker Compose.

```bash
docker-compose up --build
```

-   **API del Orquestador** estará disponible en `http://localhost:8080`.
-   **Panel de Control (UI)** (si está presente en `docker-compose.yml`) estará disponible en `http://localhost:8082`.

### Ejecutar Pruebas

Hay varios conjuntos de pruebas en el proyecto. Es **crucial** ejecutarlos después de realizar cambios.

**1. Configurar Entorno de Prueba:**
Instala todas las dependencias necesarias, incluidas las de prueba.
```bash
pip install -e ".[all,test]"
```

**2. Pruebas del Núcleo del Orquestador:**
```bash
pytest avtomatika/tests/
```

**3. Pruebas del SDK del Worker:**
```bash
pytest avtomatika-worker/tests/
```

**4. Pruebas E2E (si aplica):**
Si el proyecto tiene pruebas de extremo a extremo (E2E) usando Playwright, ejecutarlas podría requerir la preinstalación de navegadores.

```bash
# Instalar navegadores para Playwright (si no están instalados)
playwright install

# Ejecutar pruebas E2E (requiere que el sistema se esté ejecutando a través de docker-compose up)
pytest tests/e2e/
```
