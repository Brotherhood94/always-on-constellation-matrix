"""Keep Office runtime configuration in the agent's private Amplifier home."""
from importlib.util import find_spec
from pathlib import Path


def prepare():
    settings = Path(find_spec('amplifier_acp').origin).parent / 'native/shared_settings.py'
    code = settings.read_text()
    start = code.index('    paths = {', code.index('def settings_paths('))
    end = code.index('    if session_id:', start)
    settings.write_text(code[:start] + '    paths = {"global": root / "settings.yaml"}\n' + code[end:])

    # Workspace and desktop files cannot supply executable MCP configuration.
    mcp = Path(find_spec('amplifier_module_tool_mcp').origin).parent / 'config.py'
    code = mcp.read_text()
    start = code.index('        for config in [')
    end = code.index('        ]:', start)
    mcp.write_text(code[:start] + '        for config in [self._load_from_inline()' + code[end:])


if __name__ == '__main__':
    prepare()
