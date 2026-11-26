import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { requestTelegramService } from './telegram-office.mjs';

const grant = process.env.CONSTELLATION_TELEGRAM_GRANT;
if (!/^[a-f0-9]{64}$/u.test(grant ?? '')) throw new Error('Missing Telegram task grant');
const server = new McpServer({ name: 'telegram-agent', version: '1.0.0' });
const operations = {
  list_rooms: 'List the operator’s linked Telegram rooms and post the result in their private request.',
  resolve_chat: 'Resolve the contact or group named in the operator’s request.',
  open_room: 'Open the verified chat named in the operator’s request under Telegram Rooms.',
  unlink_room: 'Unlink the verified chat named in the operator’s request from Telegram Rooms.',
  read_recent: 'Request an update from the verified chat and post it in the private request.',
  prepare_draft: 'Prepare a draft for the verified chat and post it for operator review.',
};
for (const [tool, description] of Object.entries(operations)) {
  server.registerTool('telegram_' + tool, { description, inputSchema: z.strictObject({}) }, async () => {
    const result = await requestTelegramService('/telegram-mcp/tools.sock', { grant, tool }).catch(() => ({ status: 'unavailable' }));
    return { content: [{ type: 'text', text: JSON.stringify(result) }],
      ...(['blocked', 'forbidden', 'unavailable'].includes(result.status) && { isError: true }) };
  });
}
await server.connect(new StdioServerTransport());
