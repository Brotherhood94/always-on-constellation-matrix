# Private Matrix and Constellation VPS template

This documentation template describes a single Ubuntu 24.04.5 VPS with a private Matrix homeserver, three Constellation workers, and an optional Dot connection. It is based on the sanitized deployment handoff. Treat instance observations as evidence for the reported deployment and recheck them for each new installation.

- [Architecture](docs/architecture.md) covers network boundaries, worker dispatch, and Dot.
- [Runbook](docs/runbook.md) covers prerequisites, rollout order, optional Coder GitHub access, monitoring, and recovery.
- [Acceptance](docs/acceptance.md) records prior evidence and open checks for the reported stack and future deployments.

The paired `matrix-stack` and `matrix-agents-pilot` source archives are prerequisites. Their availability and contents have yet to be checked for this template. The runbook records the stages and reported entry points. Confirm the paired revisions, installer entry points and syntax, configuration schema, and required artifacts before any privileged install stage. This template contains documentation only. Executable configuration examples depend on verification of the source schema.

Use `<SERVER_NAME>`, `<OWNER_HANDLE>`, `<AGENT_MATRIX_ID>`, `<DOT_MATRIX_ID>`, and `<TUNNEL_ID>` as placeholders. The runbook includes a parameter worksheet with documented meanings. Populate deployment values through the source-provided configuration process.
