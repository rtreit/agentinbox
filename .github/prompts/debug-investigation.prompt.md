---
name: Debug Investigation
description: Investigate daemon or inbox issues
---

Investigate the current issue by checking:

1. Daemon logs: `Get-Content logs\daemon.jsonl -Tail 30`
2. Service logs: `Get-Content logs\service_stderr.log -Tail 30`
3. Task tracker: `Get-Content logs\pending_tasks.json`
4. Orphaned replies: `Get-ChildItem logs\reply_*.txt`
5. Queue connectivity: `python -m agentinbox --peek`
6. Service status: Check if the Windows service is running
7. Process list: Check for running Python/daemon processes

Diagnose the root cause and suggest a fix.
