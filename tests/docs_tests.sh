#!/bin/bash
# Documentation budget tests.
#
# The point: AGENTS.md is loaded into EVERY agent conversation, on every topic.
# It grew to 140 KB by accretion. These tests keep the split honest — new
# knowledge belongs in docs/agents/<subsystem>.md, which is loaded on demand.
#
# Usage: ./tests/docs_tests.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR" || exit 1

PASS=0; FAIL=0
ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

# 1. The root file must stay cheap. 20000 bytes ~= 5k tokens, raised from
#    12000 by the owner: the invariant list is the file's whole value and it
#    grows by one hard-won paragraph every few passes, so the budget that
#    matters is "cheap enough to load on every task", not a byte count from
#    when there were ten invariants. Still a CEILING, not a target — anything
#    naming a specific file, table or handler is subsystem knowledge and goes
#    in docs/agents/.
LIMIT=20000
SIZE=$(wc -c < AGENTS.md)
if [[ $SIZE -le $LIMIT ]]; then
    ok "AGENTS.md is ${SIZE} bytes (limit ${LIMIT})"
else
    bad "AGENTS.md is ${SIZE} bytes, over the ${LIMIT} budget.
        Move a section into docs/agents/<subsystem>.md and link it from the
        load-on-demand map. Do NOT trim the invariants to make room."
fi

# 2. Every file in docs/agents/ must be reachable from the root index,
#    or it is knowledge nobody will ever load.
for f in docs/agents/*.md; do
    base=$(basename "$f")
    [[ "$base" == "README.md" ]] && continue
    if grep -q "docs/agents/$base" AGENTS.md; then
        ok "$base is linked from AGENTS.md"
    else
        bad "$base is not referenced in AGENTS.md's load-on-demand map"
    fi
done

# 3. ...and listed in the subsystem index.
for f in docs/agents/*.md; do
    base=$(basename "$f")
    [[ "$base" == "README.md" ]] && continue
    grep -q "\`$base\`" docs/agents/README.md \
        && ok "$base is listed in docs/agents/README.md" \
        || bad "$base is missing from docs/agents/README.md"
done

# 4. No live passwords in tracked docs (AGENTS.md hard rule).
if [[ -f secrets.env ]]; then
    # shellcheck disable=SC1091
    source secrets.env
    leaked=0
    for var in AOI_OWNER_PWD ADMIN_PASSWORD BACKUP_PEER_TOKEN ZENODO_TOKEN SHARED_FILES_KEY; do
        val="${!var}"
        [[ -z "$val" || "$val" == "test2026" ]] && continue
        if grep -rqF "$val" --include="*.md" . 2>/dev/null; then
            bad "value of \$$var appears verbatim in a tracked .md file"
            leaked=1
        fi
    done
    [[ $leaked -eq 0 ]] && ok "no live secrets in *.md"
else
    ok "no secrets.env present, skipping secret scan"
fi

echo
echo "Docs tests: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
