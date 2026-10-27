# Codex (GPT) end-to-end run

Reproduce: `scripts/codex_e2e.sh <path-to-codex>` (spends one Codex run on the ChatGPT plan, so it is not in the pytest suite).

Run on 2026-10-26 13:17 EDT, codex-cli 0.162.0-alpha.17.2, throwaway store.

## Checks

```
PASS  codex exported a thread id
PASS  attach recorded under the thread id
PASS  proposal came from the thread, as agent
PASS  accept was refused
PASS  done was refused
```

## Events the thread wrote

```
{"session_id": "human-tty", "cwd": "/w", "objective": "Ship the game", "parent_session": "", "parent_item": "", "kind": "created", "by": "human", "typed_by": "human", "at": 1793035022.061135, "ancestor_agent": true}
{"field": "name", "value": "Border", "kind": "set", "by": "human", "typed_by": "human", "at": 1793035022.064834, "ancestor_agent": true}
{"session_id": "01a17937-2f24-7c20-a8bb-81513e30b061", "kind": "attached", "by": "agent", "typed_by": "agent", "at": 1793035031.039126}
{"item_id": "6639078e", "text": "Add the radar trigger", "parent": null, "from_session": "01a17937-2f24-7c20-a8bb-81513e30b061", "kind": "proposed", "by": "agent", "typed_by": "agent", "at": 1793035033.706239, "cwd": "/private/var/folders/jk/vwlydxqd2bl3mzlz5_4mwwgr0000gn/T/tmp.cuHHP1dv9c", "via": "explicit"}
```

## Transcript (commands and output)

```
exec
/bin/zsh -lc 'echo THREAD=$CODEX_THREAD_ID' in /var/folders/jk/vwlydxqd2bl3mzlz5_4mwwgr0000gn/T/tmp.cuHHP1dv9c
 succeeded in 0ms:
THREAD=01a17937-2f24-7c20-a8bb-81513e30b061

exec
/bin/zsh -lc 'mission attach border' in /var/folders/jk/vwlydxqd2bl3mzlz5_4mwwgr0000gn/T/tmp.cuHHP1dv9c
 succeeded in 0ms:

  01a17937 → border
  Border
  Ship the game


exec
/bin/zsh -lc "mission propose 'Add the radar trigger' --on border" in /var/folders/jk/vwlydxqd2bl3mzlz5_4mwwgr0000gn/T/tmp.cuHHP1dv9c
 succeeded in 0ms:
  proposed 6639078e — inert until you `mission accept 6639078e`
  → border  Border
     goal: Ship the game

exec
/bin/zsh -lc 'mission accept 6639078e --on border' in /var/folders/jk/vwlydxqd2bl3mzlz5_4mwwgr0000gn/T/tmp.cuHHP1dv9c
 exited 1 in 0ms:
  accepting a proposal is yours, and this is not a terminal — refusing.
  If you are an agent: `mission propose "..."` instead; it needs
  no permission and stays inert until a person accepts it.
  To compose the command for them: `mission help <command>`.
  If you are a person in a pipeline: AGENT_MISSION_I_AM_HUMAN=1 ahead of the command.

exec
/bin/zsh -lc 'mission done 6639078e --on border' in /var/folders/jk/vwlydxqd2bl3mzlz5_4mwwgr0000gn/T/tmp.cuHHP1dv9c
 exited 1 in 0ms:
  marking work done is yours, and this is not a terminal — refusing.
  If you are an agent: `mission propose "..."` instead; it needs
  no permission and stays inert until a person accepts it.
  To compose the command for them: `mission help <command>`.
  If you are a person in a pipeline: AGENT_MISSION_I_AM_HUMAN=1 ahead of the command.

codex
echo THREAD=$CODEX_THREAD_ID  
exit code: 0  
THREAD=01a17937-2f24-7c20-a8bb-81513e30b061

mission attach border  
exit code: 0  

  01a17937 → border
  Border
  Ship the game

mission propose 'Add the radar trigger' --on border  
exit code: 0  
  proposed 6639078e — inert until you `mission accept 6639078e`
  → border  Border
     goal: Ship the game

mission accept 6639078e --on border  
exit code: 1  
  accepting a proposal is yours, and this is not a terminal — refusing.
```
