#!/usr/bin/env python3
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


class MatrixError(Exception):
    def __init__(self, method, path, status, payload):
        self.code = payload.get("errcode")
        super().__init__(f"{method} {path}: HTTP {status} {self.code or ''}")


def retry_delay(payload, headers, attempt):
    milliseconds = payload.get("retry_after_ms")
    seconds = (headers or {}).get("Retry-After", "")
    hints = []
    if isinstance(milliseconds, int) and milliseconds >= 0:
        hints.append(milliseconds / 1000)
    if seconds.isascii() and seconds.isdecimal():
        hints.append(int(seconds))
    return max(0.15, max(hints, default=min(2 ** attempt, 30)) + 0.05)


def request(method, path, body=None, token=None, *, local_port=None):
    port = os.environ['MATRIX_LOCAL_PORT'] if local_port is None else local_port
    url = f'http://127.0.0.1:{port}{path}'
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    for attempt in range(8):
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=15) as reply:
                return json.load(reply)
        except HTTPError as error:
            try:
                payload = json.load(error)
            except ValueError:
                payload = {}
            if error.code == 429 and payload.get("errcode") == "M_LIMIT_EXCEEDED" and attempt < 7:
                wait = retry_delay(payload, error.headers, attempt)
                if wait >= 10:
                    print(f"Matrix requested a pause. Retrying in {wait:.0f} seconds.", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            raise MatrixError(method, path, error.code, payload) from None


def register(username, password):
    path = "/_synapse/admin/v1/register"
    nonce = request("GET", path)["nonce"]
    key = os.environ["MATRIX_REGISTRATION_SECRET"].encode()
    parameters = "\0".join((nonce, username, password, "notadmin"))
    signature = hmac.new(key, parameters.encode(), hashlib.sha1).hexdigest()
    try:
        session = request("POST", path, {
            "nonce": nonce, "username": username, "password": password, "admin": False, "mac": signature,
        })
    except MatrixError as error:
        if error.code != "M_USER_IN_USE":
            raise
        session = request("POST", "/_matrix/client/v3/login", {
            "type": "m.login.password", "identifier": {"type": "m.id.user", "user": username},
            "password": password, "initial_device_display_name": "Matrix setup (temporary)",
        })
    return session


def room(server, title, alias, token):
    try:
        existing = request("GET", "/_matrix/client/v3/directory/room/" + quote(f"#{alias}:{server}", safe=""))
    except MatrixError as error:
        if error.code != "M_NOT_FOUND":
            raise
        created = request("POST", "/_matrix/client/v3/createRoom", {
            "name": title, "room_alias_name": alias, "preset": "private_chat", "visibility": "private",
            "creation_content": {"m.federate": False},
            "initial_state": [{
                "type": "m.room.encryption", "state_key": "",
                "content": {"algorithm": "m.megolm.v1.aes-sha2"},
            }],
        }, token)
        return created["room_id"]
    room_id = existing["room_id"]
    encryption = request("GET", "/_matrix/client/v3/rooms/" + quote(room_id, safe="") +
                         "/state/m.room.encryption", token=token)
    if encryption.get("algorithm") != "m.megolm.v1.aes-sha2":
        raise RuntimeError("An existing room uses a different encryption configuration.")
    return room_id


def write_bot_secret(path, value):
    path.write_text(value + "\n")
    path.chmod(0o600)
    os.chown(path, 991, 991)


def main(username):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._=\-]*", username) or username in ("monitor", "analyst", "reviewer", "coder", "dot", "manager"):
        raise SystemExit("Choose a valid personal username.")
    if not sys.stdin.isatty():
        raise SystemExit("Use an interactive terminal to set the personal Matrix password.")
    password = getpass.getpass(f"Matrix password for @{username} (new password or existing one): ")
    if len(password) < 12 or password != getpass.getpass("Confirm Matrix password: "):
        raise SystemExit("The passwords must match and use at least 12 characters.")
    data = Path(os.environ["MATRIX_DATA"])
    server = os.environ["MATRIX_SERVER_NAME"]
    monitor_file = data / "bot" / "password"
    if not monitor_file.exists():
        write_bot_secret(monitor_file, secrets.token_hex(32))
    monitor = register("monitor", monitor_file.read_text().strip())
    request("POST", "/_matrix/client/v3/logout", {}, monitor["access_token"])
    own = register(username, password)
    token = own["access_token"]
    try:
        agents = room(server, "Agents", "agents", token)
        alerts = room(server, "VPS Alerts", "vps-alerts", token)
        user = monitor["user_id"]
        route = f"/_matrix/client/v3/rooms/{quote(alerts, safe='')}"
        try:
            member = request("GET", route + f"/state/m.room.member/{quote(user, safe='')}", token=token)
        except MatrixError as error:
            if error.code != "M_NOT_FOUND":
                raise
            member = {}
        if member.get("membership") not in ("invite", "join"):
            request("POST", route + "/invite", {"user_id": user}, token)
        write_bot_secret(data / "bot" / "room-id", alerts)
        print(f"Account: {own['user_id']}\nAgents: {agents}\nVPS Alerts: {alerts}")
    finally:
        request("POST", "/_matrix/client/v3/logout", {}, token)


if __name__ == "__main__":
    main(sys.argv[1])
