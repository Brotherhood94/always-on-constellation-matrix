#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from agent_catalog import REGISTRY, document as agent_catalog_document, read as read_agent_registry, workshop_profiles
from connection_history import refresh_connections

THRESHOLDS = {"CPU": (50, 45, 5), "RAM": (50, 45, 5), "Disk": (25, 22.5, 1)}
RETENTION_SECONDS = 30 * 24 * 60 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
  resource TEXT NOT NULL, time INTEGER NOT NULL, value REAL NOT NULL,
  used INTEGER, capacity INTEGER, PRIMARY KEY(resource, time)
);
CREATE TABLE IF NOT EXISTS alarms (
  resource TEXT PRIMARY KEY, active INTEGER NOT NULL, consecutive INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, time INTEGER NOT NULL, body TEXT NOT NULL);
"""


def record(db, resource, timestamp, value, used=None, capacity=None, test_thresholds=None):
    high, low, window = (test_thresholds or THRESHOLDS)[resource]
    inserted = db.execute(
        "INSERT OR IGNORE INTO samples VALUES (?, ?, ?, ?, ?)",
        (resource, timestamp, value, used, capacity),
    ).rowcount
    if not inserted:
        return
    active, consecutive = db.execute(
        "SELECT active, consecutive FROM alarms WHERE resource=?", (resource,),
    ).fetchone() or (0, 0)
    crossed = (value < low) if active else (value > high)
    consecutive = consecutive + 1 if crossed else 0
    if consecutive >= window:
        active = 1 - active
        consecutive = 0
        status = "above threshold" if active else "recovered"
        threshold = high if active else low
        stamp = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        body = f"{resource} usage {status}: {value:.1f}% (threshold {threshold:g}%). Observed {stamp}."
        db.execute("INSERT INTO outbox VALUES (?, ?, ?)", (uuid.uuid4().hex, timestamp, body))
    db.execute("INSERT OR REPLACE INTO alarms VALUES (?, ?, ?)", (resource, active, consecutive))


def command(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, text=True, timeout=25).stdout


def project_containers(project):
    return command("docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}").split()


def agent_inventory(containers, agents_root=Path("/srv/agents")):
    inventory = []
    for container in containers:
        config = container["Config"]
        labels = config.get("Labels") or {}
        service = labels.get("com.docker.compose.service", "")
        if labels.get("com.docker.compose.oneoff", "false").lower() == "true" or not re.fullmatch(r"[a-z0-9_-]+", service):
            continue
        settings = dict(entry.split("=", 1) for entry in config.get("Env", []) if "=" in entry)
        name = settings.get("AGENT_NAME")
        if not name or settings.get("AGENT_USERNAME") != service:
            continue
        state = container["State"]
        health = state.get("Health", {}).get("Status")
        status = "Stopped"
        if state.get("Restarting"):
            status = "Restarting"
        elif state.get("Running"):
            status = {"healthy": "Available", "starting": "Starting", "unhealthy": "Unavailable"}.get(health, "Running")
        model = settings.get("AGENT_MODEL")
        effort = settings.get("AGENT_REASONING")
        agent_state = agents_root / service / "state"
        try:
            engine = (agent_state / "engine").read_text().strip()
            if engine == "native":
                provider = json.loads((agent_state / "runtime" / "runtime.json").read_text())["provider"]
                model, effort = provider.get("model"), provider.get("reasoning_effort")
        except (OSError, ValueError, KeyError, TypeError):
            pass
        inventory.append({"name": name, "model": model, "effort": effort, "status": status})
    return sorted(inventory, key=lambda agent: agent["name"].casefold())


def collect_agents():
    try:
        containers = project_containers(os.environ.get("MATRIX_PROJECT", "matrix-agents"))
        return agent_inventory(json.loads(command("docker", "inspect", *containers))) if containers else []
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        print(f"Agent inventory unavailable: {error}", flush=True)
        return None


def disk_bytes(data, backups):
    own_paths = [str(data)] if backups.is_relative_to(data) else [str(data), str(backups)]
    containers = project_containers("matrix-stack") + project_containers(os.environ.get("MATRIX_PROJECT", "matrix-agents"))
    writable = 0
    if containers:
        for container in json.loads(command("docker", "inspect", "--size", *containers)):
            log = container.get("LogPath")
            if log:
                base = Path(log)
                own_paths.extend(str(p) for p in base.parent.glob(base.name + "*") if p.is_file())
            writable += container.get("SizeRw") or 0
    occupied = sum(int(line.split("\t", 1)[0]) for line in command("du", "-sxB1", *own_paths).splitlines())
    stat = os.statvfs(data)
    return occupied + writable, stat.f_blocks * stat.f_frsize


def cpu_total(cgroup):
    return int(dict(line.split() for line in (cgroup / "cpu.stat").read_text().splitlines())["usage_usec"])


def memory_capacity():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("Could not determine memory capacity")


def replace_json(path, value, mode):
    temp = path.with_suffix(".tmp")
    with temp.open("w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temp.chmod(mode)
    temp.replace(path)
    with_directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(with_directory)
    finally:
        os.close(with_directory)


def integration_status():
    project = os.environ.get("MATRIX_PROJECT", "matrix-stack")
    ids = project_containers(project) + project_containers(project + "-telegram")
    if not ids:
        return {}
    monitored = {"context": "Context Intelligence", "neo4j": "Neo4j", "synapse": "Matrix",
                 "dot": "Dot", "monitor": "VPS Alerts", "persona-workshop": "Persona Workshop",
                 "connector": "Telegram Agent", "telegram-bridge": "Telegram bridge",
                 "telegram-engine": "Telegram engine", "telegram-guide": "Telegram guide"}
    status = {}
    for item in json.loads(command("docker", "inspect", *ids)):
        name = item["Config"]["Labels"].get("com.docker.compose.service")
        if name in monitored:
            state = item["State"]
            status[monitored[name]] = state.get("Health", {}).get("Status") or state["Status"]
    return status


def export(db, outbox, acknowledgements, dashboard, timestamp, data):
    for event_id, observed, body in db.execute(
        "SELECT id, time, body FROM outbox ORDER BY time, rowid",
    ).fetchall():
        message = outbox / f"{event_id}.json"
        ack = acknowledgements / event_id
        if ack.is_file():
            message.unlink(missing_ok=True)
            with db:
                db.execute("DELETE FROM outbox WHERE id=?", (event_id,))
            ack.unlink(missing_ok=True)
        elif not message.is_file():
            replace_json(message, {"id": event_id, "time": observed, "body": body}, 0o640)
            os.chown(message, 0, 991)
    history = {}
    for resource in THRESHOLDS:
        history[resource] = db.execute(
            "SELECT time, value, used, capacity FROM samples WHERE resource=? ORDER BY time", (resource,),
        ).fetchall()
    last_backup = data / "last-successful-backup"
    page = {
        "time": timestamp, "thresholds": THRESHOLDS, "history": history,
        "agents": collect_agents(),
        "lastBackup": last_backup.read_text().strip() if last_backup.is_file() else None,
    }
    replace_json(dashboard / "metrics.json", page, 0o644)
    try:
        catalog = agent_catalog_document(read_agent_registry(), page["agents"], os.environ["MATRIX_SERVER_NAME"],
                                         timestamp, workshop_profiles(REGISTRY.parent))
        replace_json(dashboard / "agents.json", catalog, 0o644)
        replace_json(dashboard / "integrations.json", {"time": timestamp, "services": integration_status()}, 0o644)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as error:
        print(f"Agent catalog unavailable: {error}", flush=True)
    refresh_connections(data)


def run():
    data = Path(os.environ["MATRIX_DATA"])
    backup = Path(os.environ["MATRIX_BACKUP_DIR"])
    cgroup = Path("/sys/fs/cgroup/matrix.slice/matrix-workload.slice")
    state = data / "monitor"
    outbox = state / "outbox"
    acknowledgements = state / "acks"
    outbox.mkdir(mode=0o750, exist_ok=True)
    os.chown(outbox, 0, 991)
    acknowledgements.mkdir(mode=0o750, exist_ok=True)
    os.chown(acknowledgements, 991, 991)
    capacity = memory_capacity()
    cores = os.cpu_count() or 1
    with closing(sqlite3.connect(state / "metrics.sqlite3")) as db:
        db.executescript(SCHEMA)
        db.execute("PRAGMA journal_mode=WAL")
        with db:
            db.execute("UPDATE alarms SET consecutive=0 WHERE resource IN ('CPU','RAM')")
        previous_cpu = cpu_total(cgroup)
        previous_time = time.monotonic()
        last_disk = db.execute("SELECT COALESCE(MAX(time), 0) FROM samples WHERE resource='Disk'").fetchone()[0]
        scheduled = previous_time + 60
        while True:
            time.sleep(max(0, scheduled - time.monotonic()))
            now = int(time.time())
            measured = time.monotonic()
            cpu = cpu_total(cgroup)
            elapsed = measured - previous_time
            used = int((cgroup / "memory.current").read_text())
            with db:
                if 45 <= elapsed <= 90 and cpu >= previous_cpu:
                    percent = (cpu - previous_cpu) / (elapsed * 1_000_000 * cores) * 100
                    record(db, "CPU", now, percent)
                else:
                    db.execute("UPDATE alarms SET consecutive=0 WHERE resource IN ('CPU','RAM')")
                record(db, "RAM", now, 100 * used / capacity, used, capacity)
                if now - last_disk >= 3600:
                    try:
                        used, total = disk_bytes(data, backup)
                        record(db, "Disk", now, 100 * used / total, used, total)
                        last_disk = now
                    except (OSError, subprocess.SubprocessError, ValueError) as error:
                        print(f"Disk sample failed: {error}", flush=True)
                db.execute("DELETE FROM samples WHERE time < ?", (now - RETENTION_SECONDS,))
            export(db, outbox, acknowledgements, data / "dashboard", now, data)
            previous_cpu, previous_time = cpu, measured
            scheduled = measured + 60


if __name__ == "__main__":
    run()
