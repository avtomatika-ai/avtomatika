import ssl
import subprocess

import aiohttp
import pytest
from aiohttp import web

from avtomatika.config import Config
from avtomatika.constants import AUTH_HEADER_WORKER
from avtomatika.engine import OrchestratorEngine
from avtomatika.storage.memory import MemoryStorage


# Helper to run shell commands (reused from test_mtls)
def run_cmd(cmd):
    subprocess.check_call(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    """Generates a temporary PKI infrastructure for mTLS testing."""
    pki_dir = tmp_path_factory.mktemp("pki_sts")

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

    # 3. Generate Client Cert
    run_cmd(f"openssl genrsa -out {client_key} 2048")
    run_cmd(f"openssl req -new -key {client_key} -out {client_csr} -subj '/CN=worker-sts-test' -config {openssl_conf}")
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
    conf.TLS_REQUIRE_CLIENT_CERT = True  # Enforce mTLS for initial connection
    conf.API_PORT = 0
    return conf


@pytest.mark.asyncio
async def test_sts_flow(pki, mtls_config):
    """
    Test the full Security Token Service flow:
    1. Authenticate with mTLS.
    2. Request a temporary access token.
    3. Use the token to authenticate WITHOUT mTLS.
    """
    storage = MemoryStorage()
    engine = OrchestratorEngine(storage, mtls_config)

    engine.setup()
    runner = web.AppRunner(engine.app)
    await runner.setup()

    from rxon.security import create_server_ssl_context

    server_ssl_ctx = create_server_ssl_context(
        cert_path=mtls_config.TLS_CERT_PATH,
        key_path=mtls_config.TLS_KEY_PATH,
        ca_path=mtls_config.TLS_CA_PATH,
        require_client_cert=mtls_config.TLS_REQUIRE_CLIENT_CERT,
    )

    site = web.TCPSite(runner, "localhost", 0, ssl_context=server_ssl_ctx)
    await site.start()

    port = site._server.sockets[0].getsockname()[1] if site._server else 8443

    base_url = f"https://localhost:{port}"
    worker_id = "worker-sts-test"  # Must match cert CN

    # 1. Get Access Token using mTLS
    client_ssl_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=pki["ca_crt"])
    client_ssl_ctx.load_cert_chain(certfile=pki["client_crt"], keyfile=pki["client_key"])

    access_token = None

    async with (
        aiohttp.ClientSession() as session,
        session.post(f"{base_url}/_worker/auth/token", ssl=client_ssl_ctx) as resp,
    ):
        assert resp.status == 200, f"STS request failed: {await resp.text()}"
        data = await resp.json()
        access_token = data.get("access_token")
        assert access_token is not None
        assert data["worker_id"] == worker_id

    # 2. Use Access Token WITHOUT mTLS client cert (but still TLS for encryption)
    # We create a new context that trusts the server CA but has NO client cert.
    # HOWEVER, the server is configured with require_client_cert=True.
    # So we can't connect without a cert at the TCP level!

    # To test token usage, we normally would use it on a non-mTLS port or if client cert is optional.
    # If TLS_REQUIRE_CLIENT_CERT is True, then token usage is redundant for auth (except maybe scope).

    # BUT! STS is useful when:
    # A) Initial bootstrap uses mTLS, then we switch to Token (if cert not required for all requests).
    # B) We use a separate port or ingress for token-based auth.

    # Let's change the test config to verify_client=OPTIONAL, so we can connect without cert
    # but application logic will demand auth.

    await site.stop()  # Stop server to reconfigure

    # Reconfigure server to allow connections without client cert
    server_ssl_ctx.verify_mode = ssl.CERT_OPTIONAL
    site = web.TCPSite(runner, "localhost", port, ssl_context=server_ssl_ctx)
    await site.start()

    # Client context without client cert
    no_cert_ssl_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=pki["ca_crt"])

    async with aiohttp.ClientSession() as session:
        # Try to register using the Token
        headers = {AUTH_HEADER_WORKER: access_token}
        reg_data = {"worker_id": worker_id, "worker_type": "sts-worker", "supported_skills": ["t1"]}

        async with session.post(
            f"{base_url}/_worker/workers/register", json=reg_data, headers=headers, ssl=no_cert_ssl_ctx
        ) as resp:
            assert resp.status == 200, f"Token auth failed: {await resp.text()}"
            data = await resp.json()
            assert data["status"] == "registered"

    await site.stop()
    await runner.cleanup()
