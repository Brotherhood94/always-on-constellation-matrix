# Chief of Staff

Persona Workshop manages agent profiles. Context Intelligence adds Office evidence such as who took a handoff, where tasks blocked, and whether a result was verified. The Chief of Staff uses both to propose improvements with links to the supporting threads.

Select **Chief of Staff** during installation and activate it with `sudo ./constellation_matrix agent configure chief-of-staff`. Send `review team` in your encrypted one-to-one chat with Chief of Staff. A review can suggest a coordination change or propose a communication or working-style edit for a managed agent that took part in the cited work. Repeated capability gaps can lead to a hiring proposal. The operator decides which proposals to pursue.

## Managed agents

Installation and `agent add` enroll local Instances in Persona Workshop’s **Managed Agents** list. Telegram is enrolled when its Instance is prepared. Existing Workshop connections retain their target IDs. `sudo ./constellation_matrix persona-sync` backfills and refreshes connections in this deployment. It also refreshes the profile status used by the Chief of Staff.

Workshop connects to each Instance over HTTPS using its own `observe,profiles` token. Enroll a remote worker using its private management origin, token, and CA before confirming it in Office. See [remote worker setup](runbook.md#add-a-remote-worker) and the [Persona Workshop connection contract](https://github.com/microsoft/amplifier-persona-workshop/blob/df656e57d4870a51eab76d19bad8311c274bec86/docs/ui-api-contract.md#existing-agent-connections).

## Review and draft a profile change

Use the observation ID and the agent key from the Chief of Staff’s message.

```sh
sudo ./constellation_matrix persona-draft coder cos-direct-REPLACE_WITH_OBSERVATION_ID
```

This saves a Workshop revision for review and exports the portable profile, workspace instructions, and source observation. An existing-agent draft includes the managed target and the profile generation observed when it was created. Review the draft alongside the current profile before applying a revision in Workshop. Hiring proposals use a `cos-gap-` observation ID. Once approved, a proposed role can be added through `agent add` and will appear in Managed Agents.

## Hire or retire an Office worker

On a systemd VPS, setup prepares an Office-only invitation identity and enables roster management after Chief of Staff passes its readiness checks. In the encrypted Chief of Staff DM, send one of these requests.

```text
hire qa Check Office artifacts and describe how to verify them.
hire qa cos-gap-REPLACE_WITH_OBSERVATION_ID
retire qa
```

Open the proposal linked in the **Accept / Reject** poll. Check the hire profile or retirement target before voting. The first valid operator vote applies to that proposal and closes the poll. The poll expires after 15 minutes.

For an older deployment or an interrupted setup, run `sudo ./constellation_matrix setup-guide`. If setup needs to create the invitation identity, enter your Matrix operator password at the hidden prompt.

For a hire, Chief registers the worker in Office and Persona Workshop, then sends the VPS command for native Amplifier sign-in. Run it to make the worker available for assignments. A retiring worker finishes accepted assignments before Chief reports that it has been archived. [Worker lifecycle commands](runbook.md#retire-a-worker)
