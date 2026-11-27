"""Keyboard menus for the installer."""
import curses
from getpass import getpass
import os
import sys
from textwrap import wrap

ENTER = (curses.KEY_ENTER, ord('\n'), ord('\r'))


def terminal_picker(picker, *args, **kwargs):
    term = os.environ.get('TERM')
    if sys.stdin.isatty() and sys.stdout.isatty() and term and term != 'dumb':
        try:
            return curses.wrapper(picker, *args, **kwargs)
        except curses.error:
            pass
    return None


def menu(screen, title, options, default):
    keys = list(options)
    focused = keys.index(default)
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        if height < len(keys) + 4 or width < 24:
            screen.addnstr(0, 0, 'Resize the terminal to see the choices.', width - 1)
            screen.refresh()
            screen.getch()
            continue

        screen.addnstr(0, 0, title, width - 1, curses.A_BOLD)
        screen.addnstr(1, 0, 'Up/Down: move   Enter: choose', width - 1)
        for index, key in enumerate(keys):
            active = index == focused
            label = f"{'>' if active else ' '} {options[key]}"
            screen.addnstr(3 + index, 0, label, width - 1, curses.A_REVERSE if active else 0)

        screen.refresh()
        keypress = screen.getch()
        if keypress == curses.KEY_UP:
            focused = max(0, focused - 1)
        elif keypress == curses.KEY_DOWN:
            focused = min(len(keys) - 1, focused + 1)
        elif keypress in ENTER:
            return keys[focused]


def choose_option(title, options, default=None):
    default = next(iter(options)) if default is None else default
    answer = terminal_picker(menu, title, options, default)
    if answer is not None:
        return answer
    return choose_numbered_option(title, options, default)


def choose_numbered_option(title, options, default):
    keys = list(options)
    print(title)
    for number, label in enumerate(options.values(), start=1):
        print(f'  {number}. {label}')
    while True:
        number = input(f'Choice [{keys.index(default) + 1}]: ').strip()
        if not number:
            return default
        for index, key in enumerate(keys, start=1):
            if number == str(index):
                return key
        print('Choose one of the numbers above.')


def text_page(screen, title, validate, default, *, masked=False, notice=''):
    value = ''
    cursor = 0
    error = None
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        if height < 12 or width < 42:
            screen.addnstr(0, 0, 'Resize the terminal to see the entry field.', width - 1)
            screen.refresh()
            screen.get_wch()
            continue

        screen.addnstr(0, 0, title, width - 1, curses.A_BOLD)
        screen.addnstr(1, 0, 'Enter: continue', width - 1)
        field_width = min(65, width - 1)
        start = max(0, cursor - field_width + 1)
        visible = value[start:start + field_width]
        shown = '*' * len(visible) if masked else visible.encode('ascii', 'replace').decode('ascii')
        screen.addnstr(3, 0, shown.ljust(field_width), field_width, curses.A_UNDERLINE)
        messages = [(notice, curses.A_BOLD)] if notice else []
        if default and not masked:
            messages.append((f'Leave blank to keep {default}.', 0))
        if error:
            messages.append((error, 0))
        lines = [(line, style) for message, style in messages for line in wrap(message, width - 1)]
        for row, (line, style) in enumerate(lines[:height - 5], start=5):
            screen.addnstr(row, 0, line, width - 1, style)

        screen.move(3, cursor - start)
        screen.refresh()
        key = screen.get_wch()
        keycode = ord(key) if isinstance(key, str) else key
        if keycode in ENTER:
            answer = value if masked else value.strip() or default
            error = validate(answer)
            if error is None:
                return answer
        elif keycode in (curses.KEY_BACKSPACE, 8, 127):
            if cursor:
                value = value[:cursor - 1] + value[cursor:]
                cursor -= 1
        elif keycode == curses.KEY_DC:
            value = value[:cursor] + value[cursor + 1:]
        elif keycode == curses.KEY_LEFT:
            cursor = max(0, cursor - 1)
        elif keycode == curses.KEY_RIGHT:
            cursor = min(len(value), cursor + 1)
        elif keycode == curses.KEY_HOME:
            cursor = 0
        elif keycode == curses.KEY_END:
            cursor = len(value)
        elif isinstance(key, str) and (key.isprintable() or (masked and keycode >= 32)):
            value = value[:cursor] + key + value[cursor:]
            cursor += 1


def prompt_text(title, validate, *, default='', masked=False, notice=''):
    answer = terminal_picker(text_page, title, validate, default, masked=masked, notice=notice)
    if answer is not None:
        return answer
    prompt = f'{title} [{default}]: ' if default and not masked else f'{title}: '
    if notice:
        print(notice)
    while True:
        answer = getpass(prompt) if masked else input(prompt).strip() or default
        error = validate(answer)
        if error is None:
            return answer
        print(error)


def placement(key, preset):
    return preset.get('placement', 'remote' if key == 'remote-worker' else 'local')


def profile_lines(key, preset, width):
    profile = preset['profile']
    lines = [
        f"{preset['name']} ({key})",
        f"Role: {profile['role']}  |  Runs: {placement(key, preset)}",
        f"Suggested model: {preset['model']}  |  Reasoning: {preset['effort']}",
        '',
        *wrap(profile.get('persona', {}).get('purpose', ''), width),
        'Capabilities',
    ]
    for capability in profile['capabilities']:
        lines.extend(wrap(capability['description'], width, initial_indent='- ', subsequent_indent='  '))
    lines.extend(['', *wrap('Limits: ' + profile['limits'], width)])
    return lines


def checklist(screen, title, labels, initial, *, required=(), focus_done=False, describe=None):
    keys = list(labels)
    rows = [*keys, None]
    selected = (set(initial) | set(required)).intersection(keys)
    focused = len(keys) if focus_done else 0
    start = 0
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        if height < 12 or width < 42:
            screen.addnstr(0, 0, 'Resize the terminal to see the checklist.', width - 1)
            screen.refresh()
            screen.getch()
            continue

        screen.addnstr(0, 0, title, width - 1, curses.A_BOLD)
        screen.addnstr(1, 0, 'Up/Down: move   Enter: toggle or confirm', width - 1)
        screen.addnstr(2, 0, f'{len(selected)} of {len(keys)} selected', width - 1)
        # Leave room for the highlighted option's profile when one is supplied.
        visible = min(len(rows), max(1, height - (16 if describe else 4)))
        start = max(0, min(start, focused, len(rows) - visible), focused - visible + 1)
        for index in range(start, start + visible):
            key = rows[index]
            label = '    Done'
            if key is not None:
                checkbox = '[x]' if key in selected else '[ ]'
                label = f'{checkbox} {labels[key]}'
            active = index == focused
            row = f"{'>' if active else ' '} {label}"
            screen.addnstr(4 + index - start, 0, row, width - 1, curses.A_REVERSE if active else 0)

        key = rows[focused]
        if key is not None and describe:
            detail_row = 5 + visible
            details = describe(key, width - 1)
            available = height - detail_row
            if len(details) > available:
                details = details[:available - 1] + ['Enlarge the terminal for the full profile.']
            for offset, line in enumerate(details):
                screen.addnstr(detail_row + offset, 0, line, width - 1, curses.A_BOLD if offset == 0 else 0)

        screen.refresh()
        keypress = screen.getch()
        if keypress == curses.KEY_UP:
            focused = max(0, focused - 1)
        elif keypress == curses.KEY_DOWN:
            focused = min(len(rows) - 1, focused + 1)
        elif keypress in ENTER:
            if key is None:
                return [key for key in keys if key in selected]
            if key in required:
                continue
            if key in selected:
                selected.remove(key)
            else:
                selected.add(key)


def agent_checklist(screen, presets, initial=()):
    labels = {key: preset['name'] for key, preset in presets.items()}

    def describe(key, width):
        return profile_lines(key, presets[key], width)

    return checklist(screen, '🤖 Select agents to install', labels, initial, describe=describe, focus_done=bool(initial))


def select_agents(presets, initial=None):
    if not presets:
        return []
    if initial is None:
        initial = list(presets) if {'task-manager', 'hiring-manager', 'the-architect', 'coach', 'scouter'} <= presets.keys() else []
    selection = terminal_picker(agent_checklist, presets, initial)
    if selection is not None:
        return selection
    print('🤖 Available agents: ' + ', '.join(presets))
    for key, preset in presets.items():
        print('\n'.join(profile_lines(key, preset, 79)))
        print()
    choice = input('Agents to install (comma separated, Enter for defaults, none for an empty team): ').strip()
    if not choice:
        return list(initial)
    return [] if choice == 'none' else [value.strip() for value in choice.split(',') if value.strip()]


def select_integrations(initial):
    title = '🔌 Choose workspace and integrations'
    labels = {'element': 'Element workspace (required)', 'telegram': 'Telegram', 'dot': 'Dot'}
    selection = terminal_picker(checklist, title, labels, initial, required=('element',), focus_done=True)
    if selection is not None:
        return selection
    options = {
        ('element',): 'Element workspace',
        ('element', 'telegram'): 'Element workspace and Telegram',
        ('element', 'dot'): 'Element workspace and Dot',
        ('element', 'telegram', 'dot'): 'Element workspace, Telegram, and Dot',
    }
    default = tuple(key for key in labels if key == 'element' or key in initial)
    return list(choose_numbered_option(title, options, default))
