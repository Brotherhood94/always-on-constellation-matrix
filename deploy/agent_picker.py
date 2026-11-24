"""Keyboard menus for the installer."""
import curses
import os
import sys
from textwrap import wrap

ENTER = (curses.KEY_ENTER, ord('\n'), ord('\r'))


def terminal_picker(picker, *args):
    term = os.environ.get('TERM')
    if sys.stdin.isatty() and sys.stdout.isatty() and term and term != 'dumb':
        try:
            return curses.wrapper(picker, *args)
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


def placement(key, preset):
    return preset.get('placement', 'remote' if key == 'remote-worker' else 'local')


def profile_lines(key, preset, width):
    profile = preset['profile']
    lines = [
        f"{preset['name']} ({key})",
        f"Role: {profile['role']}  |  Runs: {placement(key, preset)}",
        f"Model: {preset['model']}  |  Reasoning: {preset['effort']}",
        '',
        'Capabilities',
    ]
    for capability in profile['capabilities']:
        lines.extend(wrap(capability['description'], width, initial_indent='- ', subsequent_indent='  '))
    lines.extend(['', *wrap('Limits: ' + profile['limits'], width)])
    return lines


def checklist(screen, presets, initial=()):
    keys = list(presets)
    selected = set(keys).intersection(initial)
    focused = 0
    start = 0
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        if height < 12 or width < 42:
            screen.addnstr(0, 0, 'Resize the terminal to use the agent checklist.', width - 1)
            screen.refresh()
            screen.getch()
            continue

        screen.addnstr(0, 0, 'Select agents to install', width - 1, curses.A_BOLD)
        screen.addnstr(1, 0, 'Up/Down: move   Space: toggle   Enter: done', width - 1)
        screen.addnstr(2, 0, f'{len(selected)} of {len(keys)} selected', width - 1)
        # Reserve the rest of the screen for the focused agent's profile.
        visible = min(len(keys), max(1, height - 16))
        start = max(0, min(start, focused, len(keys) - visible), focused - visible + 1)
        for index in range(start, start + visible):
            key = keys[index]
            checkbox = '[x]' if key in selected else '[ ]'
            active = index == focused
            row = f"{'>' if active else ' '} {checkbox} {presets[key]['name']}"
            screen.addnstr(4 + index - start, 0, row, width - 1, curses.A_REVERSE if active else 0)

        detail_row = 5 + visible
        details = profile_lines(keys[focused], presets[keys[focused]], width - 1)
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
            focused = min(len(keys) - 1, focused + 1)
        elif keypress == ord(' '):
            key = keys[focused]
            if key in selected:
                selected.remove(key)
            else:
                selected.add(key)
        elif keypress in ENTER:
            return [key for key in keys if key in selected]


def select_agents(presets, initial=()):
    if not presets:
        return []
    selection = terminal_picker(checklist, presets, initial)
    if selection is not None:
        return selection
    print('Available agents: ' + ', '.join(presets))
    for key, preset in presets.items():
        print('\n'.join(profile_lines(key, preset, 79)))
        print()
    return [value.strip() for value in input('Agents to install (comma separated): ').split(',') if value.strip()]
