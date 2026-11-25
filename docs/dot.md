# Connect Dot

Select Dot during the [workspace install](runbook.md#initial-setup) and keep the workspace running. The tunnel runs as a systemd service on the VPS.

## Create a tunnel and runtime key

1. **Browser.** Open Platform Tunnels in the intended OpenAI organization. Create a tunnel named `Dot`, associate it with the ChatGPT workspace that will use the app, and copy the `tunnel_…` ID. Creating the tunnel requires **Tunnels Read + Manage**. [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels)
2. **Browser.** In Runtime API Keys, create a key named `Dot VPS runtime` with **Restricted** permissions. In the **Tunnels** row, enable **Read** and **Use**. The account or principal that owns this key also needs those permissions for the tunnel. The operator copies the key privately when Platform reveals it. [Runtime API Keys](https://platform.openai.com/settings/organization/api-keys) · [Tunnel permissions](https://github.com/openai/tunnel-client/blob/master/docs/permissions.md)

## Start the tunnel on the VPS

**VPS terminal.** From the workspace checkout, substitute the copied tunnel ID. If you used a custom data directory during installation, pass the same `--data-dir` before `dot-connect`.

```sh
sudo ./constellation_matrix dot-connect tunnel_REPLACE_WITH_YOUR_ID
```

**Operator handoff.** Enter the restricted runtime key at the hidden prompt. If the deployment already has a runtime key, the command reuses it. It verifies and installs the tunnel client, enables the service at boot, and checks readiness. Continue when it prints `Dot tunnel is ready.`

## Connect and verify the private app

1. **Browser.** In the associated ChatGPT workspace, open Plugins, select **+ → Add custom MCP server**, and name it `Dot`. Under **Connection**, choose **Tunnel** and select the same tunnel or paste its ID. Choose **No authentication** for the app. The running VPS tunnel authenticates to OpenAI with the runtime key and supplies its own credentials to the local bridge. Review the app settings and select **Create as a plugin**. [ChatGPT Plugins](https://chatgpt.com/plugins) · [Custom MCP server setup](https://developers.openai.com/api/docs/guides/custom-mcp-server)
2. **ChatGPT and Element.** With the private Dot plugin enabled, publish a small Office task with a stable task ID and one available starting agent. Confirm it appears in **Agents → Office**. Repeat the publication with the same ID and confirm Dot returns the same thread link. After the agent replies, ask Dot to read the task by ID and compare its response with the Office thread.

If you enable Telegram Control later, refresh the app’s tool list to use the draft and update tools. The operator approves outbound messages in Element. [Telegram Control](telegram.md#dot-and-chat-updates)
