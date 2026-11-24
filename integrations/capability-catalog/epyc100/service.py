#!/usr/bin/env python3
import asyncio
import base64
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sys


ROOT = Path(__file__).resolve().parent
HOME = Path.home()


def settings(path):
    config = json.loads(path.read_text())
    host, owner = (config[key] for key in ("matrix_server_name", "matrix_owner"))
    if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host):
        raise ValueError("Expected the Matrix hostname on the VPS.")
    if not re.fullmatch(r"@[a-z0-9._=-]+:" + re.escape(host), owner):
        raise ValueError("Expected the operator's full Matrix ID on that homeserver.")
    socket = Path(config["tailscale_socket"]).expanduser()
    tailscale = Path(config.get("tailscale_binary", str(HOME / ".local/bin/tailscale"))).expanduser()
    if not socket.is_absolute() or not tailscale.is_absolute():
        raise ValueError("Use absolute paths for the Tailscale binary and socket.")
    return host, owner, socket, tailscale


async def copy(source, destination):
    while True:
        data = await source.read(65536)
        if not data:
            break
        destination.write(data)
        await destination.drain()
    if destination.can_write_eof():
        destination.write_eof()


async def relay(reader, writer, authorization, host, socket, tailscale):
    process = None
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 8)
        request, *headers = head.split(b"\r\n")
        valid_authorization = any(
            name.lower() == b"proxy-authorization" and hmac.compare_digest(value.strip(), authorization)
            for name, _, value in (header.partition(b":") for header in headers)
        )
        if not valid_authorization:
            writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="Matrix"\r\nConnection: close\r\n\r\n')
            await writer.drain()
            return
        target = {f"{host}:{port}".encode(): port for port in ("443", "8443")}
        method, authority, *_ = request.split() + [b"", b""]
        destination = target.get(authority)
        if method != b"CONNECT" or destination is None:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        process = await asyncio.create_subprocess_exec(
            str(tailscale), f"--socket={socket}", "nc", host, destination,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        )
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(copy(reader, process.stdin), copy(process.stdout, writer))
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
        pass
    finally:
        if process and process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            await process.wait()
        writer.close()
        await writer.wait_closed()


async def main():
    host, owner, socket, tailscale = settings(ROOT / "matrix-catalog.json")
    password = secrets.token_hex(32)
    authorization = b"Basic " + base64.b64encode(f"agent:{password}".encode())
    server = await asyncio.start_server(
        lambda reader, writer: relay(reader, writer, authorization, host, socket, tailscale),
        "127.0.0.1", 0, limit=16384,
    )
    port = server.sockets[0].getsockname()[1]
    env = {name: value for name, value in os.environ.items() if not name.lower().endswith("_proxy")}
    env.update({
        "HTTPS_PROXY": f"http://agent:{password}@127.0.0.1:{port}",
        "NODE_USE_ENV_PROXY": "1",
        "EPYC_AGENT_HOME": str(ROOT),
        "EPYC_AGENT_WORKSPACE": str(HOME),
        "EPYC_MATRIX_SERVER_NAME": host,
        "EPYC_MATRIX_OWNER": owner,
    })
    command = sys.argv[1:] or ["epyc100.mjs"]
    async with server:
        child = await asyncio.create_subprocess_exec(
            str(ROOT / "node/bin/node"), *command, cwd=ROOT / "connector", env=env,
        )
        stopping = False

        def stop():
            nonlocal stopping
            stopping = True
            if child.returncode is None:
                child.terminate()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop)
        status = await child.wait()
        return 0 if stopping else status


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(asyncio.run(main()))
