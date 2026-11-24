# Root Operations

Select Root during install and activate it with `sudo ./constellation agent sign-in root`. Root joins Office for intake and delivers approvals to the operator’s encrypted **Root Operations** direct chat. The Amplifier Instance prepares a proposal. A separate host broker executes the approved action and returns an exit-status receipt to the originating thread.

Mention Root in Office or direct a Manager handoff to Root. Review the exact operation, script, impact, and verification in the DM before voting **Accept** or **Reject**. The first valid operator vote binds to the proposal, and unanswered polls expire after 15 minutes. A revised proposal needs a fresh decision.

Run `node integrations/root/tests/protocol-test.mjs` and `bash integrations/root/tests/run` to check the approval protocol against synthetic requests and a disposable encrypted Matrix deployment.
