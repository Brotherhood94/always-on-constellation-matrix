#!/usr/bin/env python3
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

MAX_UPLOAD_SIZE = 60_000_000

def write_config(data, settings):
    synapse = data / "synapse"
    name = settings["MATRIX_SERVER_NAME"]
    url = settings["MATRIX_URL"]
    config = {
        "server_name": name,
        "public_baseurl": url + "/",
        "listeners": [{
            "type": "http", "port": 8008, "tls": False, "x_forwarded": True,
            "resources": [{"names": ["client"], "compress": False}],
        }],
        "database": {
            "name": "psycopg2", "args": {
                "user": "synapse", "password": settings["POSTGRES_PASSWORD"],
                "database": "synapse", "host": "postgres",
                "cp_min": 2, "cp_max": 8,
            },
        },
        "log_config": f"/data/{name}.log.config",
        "media_store_path": "/data/media_store",
        "max_upload_size": MAX_UPLOAD_SIZE,
        "signing_key_path": f"/data/{name}.signing.key",
        "report_stats": False,
        "enable_registration": False,
        "rc_login": {"address": {"per_second": 0.25, "burst_count": 25}},
        "federation_domain_whitelist": [],
        "allow_profile_lookup_over_federation": False,
        "url_preview_enabled": False,
        "trusted_key_servers": [],
        "registration_shared_secret": settings["MATRIX_REGISTRATION_SECRET"],
        "macaroon_secret_key": settings["MATRIX_MACAROON_SECRET"],
        "form_secret": settings["MATRIX_FORM_SECRET"],
        "user_directory": {"search_all_users": False},
    }
    destination = synapse / "homeserver.yaml"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(temporary, 0o640)
    os.chown(temporary, 991, 991)
    temporary.replace(destination)


def set_upload_limit(destination):
    config = json.loads(destination.read_text())
    if config.get("max_upload_size") == MAX_UPLOAD_SIZE:
        return
    config["max_upload_size"] = MAX_UPLOAD_SIZE
    original = destination.stat()
    with tempfile.NamedTemporaryFile(dir=destination.parent, mode="w", encoding="utf-8", delete=False) as temporary:
        replacement = Path(temporary.name)
        try:
            json.dump(config, temporary, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fchmod(temporary.fileno(), stat.S_IMODE(original.st_mode))
            if original.st_uid != os.geteuid() or original.st_gid != os.getegid():
                os.fchown(temporary.fileno(), original.st_uid, original.st_gid)
            os.fsync(temporary.fileno())
            replacement.replace(destination)
        finally:
            replacement.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--set-upload-limit":
        set_upload_limit(Path(sys.argv[2]))
    elif len(sys.argv) == 1:
        write_config(Path(os.environ["MATRIX_DATA"]), os.environ)
    else:
        sys.exit("Usage: configure.py [--set-upload-limit HOMESERVER_CONFIG]")
