import ssl
import subprocess

import aiohttp
import pytest
from aiohttp import web

from avtomatika.config import Config
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.memory import MemoryStorage


# Helper to run shell commands
def run_cmd(cmd):
    subprocess.check_call(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """Generates a temporary PKI infrastructure for mTLS testing."""
    pki_dir = tmp_path_factory.mktemp("pki")

    # Define filenames
    ca_key = pki_dir / "ca.key"
    ca_crt = pki_dir / "ca.crt"
    server_key = pki_dir / "server.key"
    server_csr = pki_dir / "server.csr"
    server_crt = pki_dir / "server.crt"
    client_key = pki_dir / "client.key"
    client_csr = pki_dir / "client.csr"
    client_crt = pki_dir / "client.crt"

    # Create OpenSSL config file
    openssl_conf = pki_dir / "openssl.cnf"
    with open(openssl_conf, "w") as f:
        f.write("""
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req

[req_distinguished_name]
commonName = Common Name

[v3_req]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment

[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
""")

    # 1. Generate CA
    run_cmd(f"openssl genrsa -out {ca_key} 2048")
    run_cmd(
        f"openssl req -x509 -new -nodes -key {ca_key} -sha256 -days 1 "
        f"-out {ca_crt} -subj '/CN=Test CA' -config {openssl_conf} -extensions v3_ca"
    )

    # 2. Generate Server Cert
    run_cmd(f"openssl genrsa -out {server_key} 2048")
    run_cmd(f"openssl req -new -key {server_key} -out {server_csr} -subj '/CN=localhost' -config {openssl_conf}")
    run_cmd(
        f"openssl x509 -req -in {server_csr} -CA {ca_crt} -CAkey {ca_key} "
        f"-CAcreateserial -out {server_crt} -days 1 -sha256 "
        f"-extfile {openssl_conf} -extensions v3_req"
    )

    # 3. Generate Client Cert (CN is the Identity)
    run_cmd(f"openssl genrsa -out {client_key} 2048")
    run_cmd(f"openssl req -new -key {client_key} -out {client_csr} -subj '/CN=worker-mtls-test' -config {openssl_conf}")
    run_cmd(
        f"openssl x509 -req -in {client_csr} -CA {ca_crt} -CAkey {ca_key} "
        f"-CAcreateserial -out {client_crt} -days 1 -sha256 "
        f"-extfile {openssl_conf} -extensions v3_req"
    )

    return {
        "ca_crt": str(ca_crt),
        "server_crt": str(server_crt),
        "server_key": str(server_key),
        "client_crt": str(client_crt),
        "client_key": str(client_key),
    }


@pytest.fixture
def mtls_config(pki):
    conf = Config()
    conf.TLS_ENABLED = True
    conf.TLS_CERT_PATH = pki["server_crt"]
    conf.TLS_KEY_PATH = pki["server_key"]
    conf.TLS_CA_PATH = pki["ca_crt"]
    conf.TLS_REQUIRE_CLIENT_CERT = True
    conf.API_PORT = 0  # Random port
    # Disable background tasks for speed
    conf.WATCHER_INTERVAL_SECONDS = 9999
    return conf


@pytest.mark.asyncio
async def test_mtls_worker_authentication(pki, mtls_config):
    """
    Verifies that a worker connecting with a valid certificate is authenticated
    using the CN from the certificate as the worker_id.
    """
    storage = MemoryStorage()
    engine = OrchestratorEngine(storage, mtls_config)

    # Start the server manually to get the real port
    engine.setup()
    runner = web.AppRunner(engine.app)
    await runner.setup()

    # Create SSL Context for server
    from rxon.security import create_server_ssl_context

    server_ssl_ctx = create_server_ssl_context(
        cert_path=mtls_config.TLS_CERT_PATH,
        key_path=mtls_config.TLS_KEY_PATH,
        ca_path=mtls_config.TLS_CA_PATH,
        require_client_cert=mtls_config.TLS_REQUIRE_CLIENT_CERT,
    )

    site = web.TCPSite(runner, "localhost", 0, ssl_context=server_ssl_ctx)
    await site.start()

    # Get the assigned port
    # Accessing internal _server is a bit hacky but needed for 0 port
    port = site._server.sockets[0].getsockname()[1] if site._server else 8443

    base_url = f"https://localhost:{port}"

    # --- Client Side Setup ---
    # Create SSL Context for client
    client_ssl_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=pki["ca_crt"])
    client_ssl_ctx.load_cert_chain(certfile=pki["client_crt"], keyfile=pki["client_key"])

    # The CN in client cert is "worker-mtls-test"
    # We will try to register or ping.
    # Note: Orchestrator expects worker_id in URL or Body usually.
    # But middleware should populate request['worker_id'] from cert.

    # Let's try to hit an endpoint that requires auth.
    # update_worker_status (PATCH /_worker/workers/{worker_id})
    # If auth works, it should complain "Worker not found" (404) or succeed if we register first.
    # If auth fails, it would be 401.

    # First, we need to register the worker. Registration normally takes worker_id in body.
    # With mTLS, the middleware extracts ID from cert.
    # Let's see if middleware logic handles this.
    # worker_auth_middleware checks:
    # 1. extract_cert_identity -> "worker-mtls-test"
    # 2. sets request["worker_id"] = "worker-mtls-test"
    # 3. passes control to handler.

    # The handler `register_worker_handler` looks at `request.get("worker_registration_data")`
    # which comes from body middleware check.
    # But middleware only reads body if path ends with /register AND worker_id not in path.

    # Let's try a simpler endpoint: GET /_worker/workers/{worker_id}/tasks/next
    # If we pass worker_id in URL matching the cert, it should work.
    # Actually, the middleware override `request["worker_id"]` from cert.
    # So even if we request `/tasks/next` it should be authenticated.

    target_worker_id = "worker-mtls-test"

    async with aiohttp.ClientSession() as session:
        # 1. Register (POST)
        # Note: We do NOT send X-Worker-Token header.
        reg_data = {"worker_id": target_worker_id, "worker_type": "tls-worker", "supported_tasks": ["t1"]}
        async with session.post(f"{base_url}/_worker/workers/register", json=reg_data, ssl=client_ssl_ctx) as resp:
            assert resp.status == 200, f"Registration failed: {await resp.text()}"
            data = await resp.json()
            assert data["status"] == "registered"

        # 2. Verify worker is in storage
        info = await storage.get_worker_info(target_worker_id)
        assert info is not None
        assert info["worker_type"] == "tls-worker"

        # 3. Heartbeat (PATCH)
        # We access URL with worker_id. Middleware should confirm cert CN == worker_id logic?
        # Actually, my middleware implementation simply sets request["worker_id"] = cert_id.
        # It DOES NOT check if URL param matches cert param yet. Ideally it should or handler relies
        # on request["worker_id"]

        async with session.patch(f"{base_url}/_worker/workers/{target_worker_id}", ssl=client_ssl_ctx) as resp:
            assert resp.status == 200, f"Heartbeat failed: {await resp.text()}"

    await site.stop()
    await runner.cleanup()
