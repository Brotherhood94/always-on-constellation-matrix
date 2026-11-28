# Connect Dot

Select Dot during the [workspace install](runbook.md#initial-setup). The terminal prints this guide after Element sign-in and includes it in `./constellation_matrix setup-guide --print`. The Docker control runtime starts the tunnel on the VPS.

## Create a tunnel and runtime key

1. **Browser.** Open Platform Tunnels in the intended OpenAI organization. Create a tunnel named `Dot`, associate it with the ChatGPT workspace that will use the app, and copy the `tunnel_…` ID. Creating the tunnel requires **Tunnels Read + Manage**. [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels)
2. **Browser.** In Runtime API Keys, create a key named `Dot VPS runtime` with **Restricted** permissions. In the **Tunnels** row, enable **Read** and **Use**. The account or principal that owns this key also needs those permissions for the tunnel. The operator copies the key privately when Platform reveals it. [Runtime API Keys](https://platform.openai.com/settings/organization/api-keys) · [Tunnel permissions](https://github.com/openai/tunnel-client/blob/master/docs/permissions.md)

## Start the tunnel on the VPS

**VPS terminal.** From the workspace checkout, substitute the copied tunnel ID. If you used a custom data directory during installation, pass the same `--data-dir` before `dot-connect`.

```sh
./constellation_matrix dot-connect tunnel_REPLACE_WITH_YOUR_ID
```

**Operator handoff.** Enter the restricted runtime key at the hidden prompt. If the deployment already has a runtime key, the command reuses it. It verifies and installs the tunnel client, starts the managed service, and checks readiness. Continue when it prints `Dot tunnel is ready.`

## Connect and verify the private app

1. **Browser.** In the associated ChatGPT workspace, open Plugins, select **+ → Add custom MCP server**, and name it `Constellation Office`. Under **Connection**, choose **Tunnel** and select the same tunnel or paste its ID. Choose **No authentication** for the app. The running VPS tunnel authenticates to OpenAI with the runtime key and supplies its own credentials to the local bridge. Review the app settings and select **Create as a plugin**. [ChatGPT Plugins](https://chatgpt.com/plugins) · [Custom MCP server setup](https://developers.openai.com/api/docs/guides/custom-mcp-server)
2. **ChatGPT.** Ask Dot to run `list_agents` through the plugin and compare the result with your Office roster.
3. **ChatGPT and Element.** Once Task Manager is available, ask “Dot, send this task to Office starting with Task Manager: coordinate a three-step checklist for getting started and post the result in the Office thread.” Check the task and Manager’s result in **Agents → Office**. A retry with the same task ID should return the same thread link.

If you enable Telegram Agent access for Dot later, refresh the app’s tool list to use the draft and update tools. Drafts provide a preview. Explicit sends through Task Manager go to the verified Telegram chat. [Telegram Agent](telegram.md#dot-and-chat-updates)

To route a Telegram task through Office, ask “Dot, ask Task Manager in Office to send a private Telegram message to Sam Example saying «Hello».” Dot uses `publish_telegram_task` to pass the request to Task Manager and reports the result from the original Office thread. The same tool can open a chat, manage a group or request a conversation summary. The chat type is optional. After an upgrade, refresh the app’s tool list if the new tool is missing. [Telegram from Office](telegram.md#use-telegram-from-office)

## Control Room

Ask Dot to search the encrypted Control Room history or request an operation from The Architect or Telegram Agent. Dot reads messages it can decrypt and identifies gaps when encryption keys are unavailable. Use Element to cast approvals. For Telegram delivery, ask Dot to send the task to Office and name Telegram Agent explicitly if you want to address it directly.
