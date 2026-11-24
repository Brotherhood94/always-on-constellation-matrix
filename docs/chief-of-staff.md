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
