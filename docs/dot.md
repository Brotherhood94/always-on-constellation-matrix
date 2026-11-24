# Connect Dot

Select Dot during the [administrator install](runbook.md#administrator-install). Create a Secure MCP Tunnel in your OpenAI Platform organization and generate a runtime key with access to that tunnel. Use the VPS operator’s interactive terminal for setup.

```sh
sudo ./constellation_matrix dot-connect tunnel_REPLACE_WITH_YOUR_ID
```

The command verifies the pinned runtime archive, installs an outbound tunnel service, and checks readiness. Store the runtime key through its hidden prompt. Create or reconnect your private MCP app using **Tunnel** as its connection type and the same tunnel ID. Refresh its tool list after enabling Telegram Control. [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) · [Connect a private MCP app](https://developers.openai.com/api/docs/guides/custom-mcp-server)

Test by publishing a small plan with a stable task ID and one starting agent. Confirm it appears in Office. Retry the same request and confirm the thread link is identical. Once the agent has replied, retrieve the task by ID. Dot can request a Telegram draft or update when Telegram Control is connected. The operator approves sending in Element.
