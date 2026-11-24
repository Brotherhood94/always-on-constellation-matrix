"""Choose which preset agents to provision from an interactive terminal."""
import curses
import os
import sys
from textwrap import wrap


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


def checklist(screen, presets):
    keys = list(presets)
    selected = set()
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
        elif keypress in (curses.KEY_ENTER, ord('\n'), ord('\r')):
            return [key for key in keys if key in selected]


def select_agents(presets):
    if not presets:
        return []
    term = os.environ.get('TERM')
    if sys.stdin.isatty() and sys.stdout.isatty() and term and term != 'dumb':
        try:
            return curses.wrapper(checklist, presets)
        except curses.error:
            print('Terminal checklist unavailable. Enter agent IDs below.')
    print('Available agents: ' + ', '.join(presets))
    for key, preset in presets.items():
        print('\n'.join(profile_lines(key, preset, 79)))
        print()
    return [value.strip() for value in input('Agents to install (comma separated): ').split(',') if value.strip()]
