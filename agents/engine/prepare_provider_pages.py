"""Add terminal page breaks at presentation boundaries in the pinned CLI."""
from importlib.util import find_spec
from pathlib import Path


def insert_before(code, anchor, lines):
    if code.count(anchor) != 1:
        raise ValueError(f'Expected one Amplifier setup page boundary at {anchor.strip()}')
    return code.replace(anchor, lines + anchor, 1)


def prepare(root):
    commands = root / 'commands/provider.py'
    code = commands.read_text()
    for anchor in (
        '    console.print("\\n  [bold]Available providers:[/bold]")\n',
        '    console.print(\n        f"\\n  [green]✓ Provider added:',
        '    console.print(f"\\n  [green]✓ Provider updated:',
    ):
        code = insert_before(code, anchor, '    console.clear()\n')
    commands.write_text(code)

    fields = root / 'provider_config_utils.py'
    code = fields.read_text()
    for anchor in (
        '    # Show field info\n',
        '    display_name = info.get("display_name", provider_id)\n    console.print()\n    proceed = Confirm.ask(',
        '            if login_provider is not None:\n',
    ):
        indent = anchor[:len(anchor) - len(anchor.lstrip())]
        code = insert_before(code, anchor, indent + 'console.clear()\n')
    failure = '    return provider\n\n\ndef _should_show_field'
    code = insert_before(code, failure,
                         '        if console.is_terminal:\n            console.input("Press Enter to continue. ")\n')
    fields.write_text(code)


if __name__ == '__main__':
    prepare(Path(find_spec('amplifier_app_cli').origin).parent)
