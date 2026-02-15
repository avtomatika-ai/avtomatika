import asyncio
import os
import uuid

from aiohttp import web

from avtomatika.constants import AUTH_HEADER_WORKER

routes = web.RouteTableDef()

WORKER_ID = f"test-worker-{uuid.uuid4()}"
AVTOMATIKA_URL = os.getenv("AVTOMATIKA_URL", "http://localhost:8080")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "secure-worker-token")
WORKER_PORT = int(os.getenv("WORKER_PORT", 9999))


async def register_with_avtomatika(app):
    """Periodically registers the worker with the orchestrator."""
    worker_payload = {
        "worker_id": WORKER_ID,
        "worker_type": "e2e_test_worker",
        "supported_skills": ["error_task"],
        "status": "idle",
    }
    headers = {AUTH_HEADER_WORKER: WORKER_TOKEN}

    while True:
        try:
            async with app["client_session"].post(
                f"{AVTOMATIKA_URL}/_worker/workers/register",
                json=worker_payload,
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    print(f"Successfully registered as worker {WORKER_ID}")
                else:
                    print(f"Failed to register. Status: {resp.status}, Body: {await resp.text()}")
        except Exception as e:
            print(f"Could not connect to avtomatika to register: {e}")

        await asyncio.sleep(10)  # Re-register every 10 seconds


async def poll_for_tasks(app):
    """Polls the orchestrator for new tasks."""
    headers = {AUTH_HEADER_WORKER: WORKER_TOKEN}
    while True:
        try:
            async with app["client_session"].get(
                f"{AVTOMATIKA_URL}/_worker/workers/{WORKER_ID}/tasks/next",
                headers=headers,
                timeout=30,
            ) as resp:
                if resp.status == 200:
                    task = await resp.json()
                    print(f"Received task: {task['task_id']}")
                    asyncio.create_task(handle_task(app, task))
                elif resp.status != 204:
                    print(f"Error polling for tasks. Status: {resp.status}")
        except asyncio.TimeoutError:
            continue  # Normal for long polling
        except Exception as e:
            print(f"Exception while polling for tasks: {e}")
            await asyncio.sleep(5)


async def handle_task(app, task):
    """Simulates task execution and reports the result back."""
    task_type = task.get("type")
    error_type = task["params"].get("error_type", "SUCCESS")
    print(f"Handling task {task['task_id']} ({task_type}) with requested error type: {error_type}")

    result = {}
    if task_type == "file_processing_task":
        input_meta = task["params"].get("input_file_meta", {})
        if input_meta:
            print(f"Worker: Validating input file metadata: {input_meta}")
            if input_meta.get("size") == -1:
                result = {
                    "status": "failure",
                    "error": {"code": "INVALID_INPUT_ERROR", "message": "File size mismatch"},
                }

        if not result:
            result = {
                "status": "success",
                "data": {
                    "message": "File processed",
                    "output_file_meta": {
                        "size": 1024,
                        "etag": "simulated_etag_hash",
                        "s3_uri": "s3://bucket/results/output.bin",
                    },
                },
            }

    elif error_type == "SUCCESS":
        result = {"status": "success", "data": {"message": "Task completed successfully"}}
    else:
        result = {
            "status": "failure",
            "error": {"code": error_type, "message": f"Simulated {error_type}"},
        }

    response_payload = {
        "job_id": task["job_id"],
        "task_id": task["task_id"],
        "worker_id": WORKER_ID,
        "result": result,
    }
    headers = {AUTH_HEADER_WORKER: WORKER_TOKEN}

    try:
        async with app["client_session"].post(
            f"{AVTOMATIKA_URL}/_worker/tasks/result",
            json=response_payload,
            headers=headers,
        ) as resp:
            if resp.status != 200:
                print(f"Failed to report task result. Status: {resp.status}")
            else:
                print(f"Successfully reported result for task {task['task_id']}")
    except Exception as e:
        print(f"Exception while reporting task result: {e}")


@routes.get("/status")
async def status(request):
    return web.json_response({"status": "ok", "worker_id": WORKER_ID})


async def on_startup(app):
    import aiohttp

    app["client_session"] = aiohttp.ClientSession()
    app["poller"] = asyncio.create_task(poll_for_tasks(app))
    app["register"] = asyncio.create_task(register_with_avtomatika(app))


async def on_shutdown(app):
    app["poller"].cancel()
    app["register"].cancel()
    await app["client_session"].close()


if __name__ == "__main__":
    app = web.Application()
    app.add_routes(routes)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host="0.0.0.0", port=WORKER_PORT)
