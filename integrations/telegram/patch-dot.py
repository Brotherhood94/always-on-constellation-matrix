from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
text = source.read_text()
marker = "import { createTelegramTools } from './dot-telegram.mjs';"
if marker not in text:
    import_line = "import { openRoomClient, sendEncrypted } from './matrix-session.mjs';"
    bootstrap = 'function mcpServer() {'
    registry = '  return server;'
    for anchor in (import_line, bootstrap, registry):
        if text.count(anchor) != 1:
            raise SystemExit('The installed Dot source does not match the supported MCP server layout.')
    text = text.replace(import_line, import_line + '\n' + marker)
    text = text.replace(bootstrap, 'const telegram = await createTelegramTools({ state, serverName, userId, db, sendEncrypted,\n'
                        '  getClient: () => client, z });\n' + bootstrap)
    text = text.replace(registry, '  telegram.register(server, taskId, response, failure);\n' + registry)
output.write_text(text)
