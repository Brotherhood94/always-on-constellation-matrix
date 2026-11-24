# Delegated runtime failures

Apply `bash install` as root after the compatible [capability catalog rollout](../../docs/runbook.md#agent-capability-catalog). The installer checks the connector baselines and reports the backup location. It updates and restarts the Coder and Manager services.

In an assignment delegated by Manager, Coder returns the failed ACP stage, safe diagnostic, and any available attachment to Manager in the original thread. The receipt identifies a recovery step. Manager decides whether the work can continue and publishes the verified outcome for the assignment.

In a disposable Matrix deployment, make Coder's fixture runtime fail during the prompt after returning a partial artifact. Confirm the reply mentions Manager, retains the attachment reference and gives the failure and recovery action. Check that the original task stays open until Manager issues its outcome.
