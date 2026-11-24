import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import UUID


sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'deploy'))
from persona_management import api


SECTIONS = {
    "communication": {"voice", "phrasing", "structure", "tone", "context", "editing"},
    "working": {"framing", "decisions", "execution", "collaboration", "feedback", "tradeoffs"},
}
UNSAFE_REFERENCE = re.compile(r"https?:|mxc:|file:|[a-f0-9]{24,}|[@#][^\s]+:[^\s]+", re.I)


def read_guidance(path):
    source = path.read_text().strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", source, flags=re.S | re.I)
    draft = json.loads(fence[1] if fence else source)
    if not isinstance(draft, dict):
        raise ValueError("Amplifier returned an invalid persona draft")
    if draft.get("kind") == "insufficient_evidence":
        return []
    guidance = draft.get("guidance")
    if draft.get("kind") != "persona_draft" or not isinstance(guidance, list) or not 2 <= len(guidance) <= 4:
        raise ValueError("Amplifier returned an invalid persona draft")
    rules = []
    for item in guidance:
        if not isinstance(item, dict):
            raise ValueError("Invalid persona guidance")
        collection, section, statement = (item.get(field) for field in ("collection", "section", "statement"))
        if (
            not isinstance(collection, str) or collection not in SECTIONS
            or not isinstance(section, str) or section not in SECTIONS[collection]
            or not isinstance(statement, str) or not 5 <= len(statement.strip()) <= 600
            or UNSAFE_REFERENCE.search(statement) or any(ord(char) < 32 for char in statement)
        ):
            raise ValueError("Invalid persona guidance")
        rules.append({"collection": collection, "section": section, "statement": statement.strip()})
    if {rule["collection"] for rule in rules} != set(SECTIONS):
        raise ValueError("Draft guidance must cover communication and working style")
    return rules


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def compile_draft(container, role, run, export, managed=None):
    if (export / "profile.persona.json").is_file():
        print(f"Persona Workshop draft already exported at {export}")
        return
    evidence = json.loads((run / "observation.json").read_text())
    guidelines = read_guidance(run / "draft.json")
    if not guidelines:
        print("The selected observation does not support a persona draft for this role.")
        return
    observation = evidence["observation_id"]
    key = hashlib.sha256(f"{role}\0{observation}".encode()).hexdigest()[:20]
    title = f"{role.title()} draft {key}"
    root = "/api/v1/projects"
    rows = [row for row in api(container, "GET", root)["items"] if row["alias"] == title]
    if len(rows) > 1:
        raise ValueError("More than one Workshop draft matches this observation")
    if rows:
        project = api(container, "GET", f"{root}/{UUID(rows[0]['id'])}")
    else:
        project = api(container, "POST", root, {
            "alias": title, "purpose": f"Reviewable guidance for the {role} role.",
            "subject_type": "fictional", "aliases": [],
        })
    resource = f"{root}/{UUID(project['id'])}"
    for rule in guidelines:
        same = [entry for entry in project["observations"]
                if entry["valid"] and all(entry.get(field) == value for field, value in rule.items())]
        if same:
            continue
        project = api(container, "POST", resource + "/observations", {
            **rule,
            "expected_revision": project["revision"],
            "scope": f"Chief of Staff proposal for {role}. Operator review before use.",
        })
        added = next(entry for entry in project["observations"]
                     if all(entry.get(field) == value for field, value in rule.items()))
        project = api(container, "PUT", resource + f"/observations/{UUID(added['id'])}", {
            "expected_revision": project["revision"], "status": "pending",
        })

    result = api(container, "POST", resource + "/revisions", {"expected_revision": project["revision"]})
    if result.get("status") != "draft" or not result.get("valid"):
        raise ValueError("Workshop did not compile a valid draft")
    revision = f"{resource}/revisions/{UUID(result['id'])}"
    native = api(container, "GET", revision + "/export")
    agents = api(container, "GET", revision + "/exports/agents")
    if native.get("schema") != "twin-profile/1" or agents.get("schema") != "persona-workshop.export/1":
        raise ValueError("Unexpected Persona Workshop export format")
    with TemporaryDirectory(dir=export.parent, prefix=".draft-") as directory:
        temp = Path(directory)
        staged = temp / "export"
        staged.mkdir(mode=0o700)
        write_json(staged / "profile.persona.json", native)
        write_json(staged / "agents-workspace.json", agents)
        for name in ("communication.md", "working-style.md"):
            (staged / name).write_text(result["files"][name])
        write_json(staged / "draft-origin.json", {
            "role": role, "observation_id": observation, "sources": evidence.get("sources", []),
            "workshop_project": project["id"], "workshop_revision": result["id"], "status": "awaiting_review",
            **({"workshop_target": managed["target"], "target_profile_revision": managed["revision"]} if managed else {}),
        })
        staged.rename(export)
    print(f"Persona Workshop draft ready for review at {export}")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        compile_draft(sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]),
                      json.loads(sys.argv[5]) if len(sys.argv) > 5 else None)
    except subprocess.CalledProcessError as error:
        sys.exit(error.stderr.strip() or "Persona Workshop API request failed")
    except (ValueError, OSError) as error:
        sys.exit(str(error))
