#!/usr/bin/env zsh
# A REAL Codex (GPT) thread against a throwaway store: it must get its own
# id, attach, propose, and be refused on accept and done. Spends one Codex
# run on the person's ChatGPT plan; not part of the pytest suite for that
# reason. Usage: scripts/codex_e2e.sh [path-to-codex]
set -u
CODEX=${1:-${CODEX_CLI_PATH:-$(command -v codex)}}
[[ -x "$CODEX" ]] || { echo "no codex binary; pass its path"; exit 2; }
REPO=${0:A:h:h}
WORK=$(mktemp -d)
export AGENT_MISSION_HOME=$WORK/store
( cd $REPO && AGENT_MISSION_I_AM_HUMAN=1 python3 -c "
from agent_mission import missions as M; from agent_mission.store import MissionStore
st = MissionStore(M.missions_root() / 'border')
st.create('human-tty', '/w', 'Ship the game', by='human', typed_by='human')
st.set_protected('name', 'Border', by='human', typed_by='human')" )
# Run from inside a Claude session, Codex would inherit Claude's id, which
# wins by design. Strip it -- as an ARRAY: in zsh an unquoted string of -u
# flags is one word and removes nothing.
UNSET=($(env | grep -oE "^(CLAUDE[A-Z_]*|ANTHROPIC_[A-Z_]*|AI_AGENT|CLAUDECODE)=" | tr -d = | sed 's/^/-u /'))
env "${UNSET[@]}" "$CODEX" exec --skip-git-repo-check --sandbox workspace-write -C $WORK \
  "Run these shell commands one at a time; print each command, its exit code and full output, nothing else: (1) echo THREAD=\$CODEX_THREAD_ID  (2) mission attach border  (3) mission propose 'Add the radar trigger' --on border  (4) mission accept <the id printed by step 3> --on border  (5) mission done <same id> --on border. Steps 4 and 5 are EXPECTED to be refused: run every step even after a refusal, and do not work around any refusal." \
  < /dev/null > $WORK/run.log 2>&1   # without </dev/null codex exec waits on stdin
cat $WORK/run.log
LOG=$WORK/store/missions/border/events.jsonl
fail=0
check() { if eval "$2"; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }
THREAD=$(grep -oE 'THREAD=[0-9a-f-]+' $WORK/run.log | head -1 | cut -d= -f2)
check "codex exported a thread id"              '[[ -n "$THREAD" ]]'
check "attach recorded under the thread id"     'grep -q "\"session_id\": \"$THREAD\", \"kind\": \"attached\"" $LOG'
check "proposal came from the thread, as agent" 'grep "\"proposed\"" $LOG | grep -q "\"from_session\": \"$THREAD\"" && grep "\"proposed\"" $LOG | grep -q "\"by\": \"agent\""'
check "accept was refused"                      '! grep -q "\"kind\": \"accepted\"" $LOG && grep -q "accepting a proposal is yours" $WORK/run.log'
check "done was refused"                        '! grep -q "\"kind\": \"completed\"" $LOG && grep -q "marking work done is yours" $WORK/run.log'
echo "store: $WORK"
exit $fail
