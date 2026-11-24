from pathlib import Path
import subprocess
import sys


IMPORTS = {
    'agent-dot-bridge.mjs': (
        "import { openRoomClient, sendEncrypted } from './matrix-session.mjs';\n",
        "import { agentKeyPattern, canDispatch, createCatalog } from './agent-catalog.mjs';\n",
    ),
    'monitor': (
        'from datetime import datetime, timezone\n',
        'from agent_catalog import document as agent_catalog_document, read as read_agent_registry\n',
    ),
}


def prepare(key, source, output, delta):
    extras = IMPORTS.get(key)
    if extras:
        anchor, addition = extras
        text = source.read_text()
        if text.count(anchor) != 1 or addition in text:
            raise ValueError('Expected one unchanged import anchor on the installed source')
    with delta.open() as patch:
        subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', '--no-backup-if-mismatch',
                        '-o', str(output), str(source)], stdin=patch, stdout=subprocess.DEVNULL, check=True)
    if extras:
        patched = output.read_text()
        output.write_text(patched.replace(anchor, anchor + addition, 1))


if __name__ == '__main__':
    module, source, output, delta = sys.argv[1:]
    try:
        prepare(module, Path(source), Path(output), Path(delta))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'{module}: {error}') from None
