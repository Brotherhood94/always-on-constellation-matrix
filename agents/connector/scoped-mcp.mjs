import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { registerTeamTools } from './team-tools.mjs';

const server = new McpServer({name:'office-approved-tools', version:'1.0.0'});
registerTeamTools(server);
await server.connect(new StdioServerTransport());
