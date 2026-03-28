---
applyTo: "**"
---

# Session Startup

At the **very start of every session**, before doing anything else, run:

```powershell
& src\agentinbox\ensure_daemon.ps1
```

This checks if the agent daemon is running and starts it if not. The daemon
handles incoming GroupMe directives in the background by spawning separate
executor sessions, so your interactive session stays responsive.

After running the startup script, check the daemon log for any recent
background activity:

```powershell
Get-Content logs\daemon.jsonl -Tail 10 -ErrorAction SilentlyContinue
```

Then check the inbox:

```powershell
python -m agentinbox
```

Then proceed with whatever task the user requested.
