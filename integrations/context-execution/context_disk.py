import json
from pathlib import Path


PROJECT = "matrix-context"
VOLUMES = {
    "matrix-context_context-intelligence-data",
    "matrix-context_context-intelligence-neo4j-data",
    "matrix-context_context-intelligence-v2-data",
    "matrix-context_context-intelligence-v2-neo4j-data",
}


def add_memory_volumes(roots, command):
    volume_names = command(
        "docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}",
    ).split()
    selected = sorted(VOLUMES.intersection(volume_names))
    if not selected:
        return
    volumes = json.loads(command("docker", "volume", "inspect", *selected))
    covered = [Path(root).resolve() for root in roots]
    for volume in volumes:
        path = Path(volume["Mountpoint"])
        if volume["Driver"] != "local" or not path.is_absolute() or path == Path("/"):
            raise ValueError("Unexpected Context Intelligence Docker volume")
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in covered):
            roots.append(str(path))
            covered.append(path)
