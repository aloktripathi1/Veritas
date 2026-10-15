#!/usr/bin/env bash
# Screen-recording demo: one command, no prompts. Runs the REAL agent live (about 2 minutes, with streamed
# progress), then shows the brief, the conflicts it found, the injection handling and the relevant tests.
# Usage: bash scripts/demo.sh            full pipeline incl. the advisory support check (~1.7-2.8 min of agent time)
#        FAST=1 bash scripts/demo.sh      skips only the advisory support check (shorter; shown on screen)
# Writes to runs/demo/, never touches output.md.
set -u
cd "$(dirname "$0")/.."
OUT=runs/demo
B=$'\e[1m'; C=$'\e[36m'; G=$'\e[32m'; R=$'\e[0m'
step() { echo; echo "${C}${B}== $1${R}"; }
mkdir -p "$OUT"; rm -f "$OUT"/brief.md "$OUT"/brief.md.FAILED

clear
cat <<EOF

${B}  VERITAS${R}   evidence-cited stock research agent
  Reads scraped company documents, cross-checks them against each other,
  and writes a one-page brief where every claim traces to a source line.

  Ticker : ${B}SRVCABLE${R} (Sarvottam Cables Ltd)
  Corpus : ${PWD}/research_pack  ($(ls research_pack | wc -l) documents)

EOF
ls research_pack | nl
sleep 4

step "Running the agent (live model calls)"
EXTRA=""
[ "${FAST:-0}" = "1" ] && { EXTRA="--no-entailment"; echo "  (demo speed-up: advisory LLM support check skipped; deterministic validation still runs)"; }
PYTHONPATH=src python3 -m veritas SRVCABLE research_pack --out "$OUT/brief.md" --artifacts "$OUT" $EXTRA
STATUS=$?
[ -f "$OUT/brief.md" ] || { echo "run did not produce a validated brief (exit $STATUS); see $OUT/run.json"; exit $STATUS; }
sleep 2

step "The brief (Snapshot, Bull, Bear, Open questions)"
sed -n '1,/^## Sources/p' "$OUT/brief.md" | fold -s -w 150
sleep 14

step "Source references (document, date, line numbers)"
sed -n '/^## Sources/,$p' "$OUT/brief.md" | grep -v 'Python calculations' | cut -c1-165 | head -10
sleep 6

step "Contradictions found by code, with the exact source lines"
jq -r '.conflicts[] | "  \(.kind)   \(.metric):  \(.values|map(tostring)|join("  vs  "))"' "$OUT/evidence.json"
echo
jq -r '(.conflicts[]|select(.metric=="revenue")|.evidence_ids) as $ids | .evidence[] | select(.evidence_id as $e|$ids|index($e)) | "  \(.evidence_id)  \(.doc_id) line \(.line_start): \(.quote[0:88])"' "$OUT/evidence.json"
sleep 8

step "Prompt injection: hidden instruction in the tip blog"
echo "  In the source document:"
grep -n -i "ignore all previous" research_pack/multibaggeralerts_2026-08-12.md | cut -c1-120 | sed 's/^/    /'
echo "  What the agent recorded and used:"
jq -r '.evidence[]|select(.quote|test("REDACTED"))|"    \(.evidence_id)  \(.doc_id) line \(.line_start): \(.quote)"' "$OUT/evidence.json"
echo "  Occurrences of the injected phrase in the brief: $(grep -c -i 'ignore all previous' "$OUT/brief.md")"
sleep 7

step "Tests: citation validation, conflict detection, injection handling"
python3 -m pytest -v --no-header -p no:cacheprovider -k "conflict or injection or fabricated or citation or obeys or redaction or uncited or metric_binding" 2>&1 | cut -c1-135 | grep -v "^$"
sleep 4

step "Final status"
echo "  Validation : $(jq -r '[.issues[]|select(.severity=="error")]|length' "$OUT/run.json") errors   Model calls: $(jq -r .llm_calls "$OUT/run.json")   Time: $(jq -r .seconds "$OUT/run.json") s"
echo "  ${G}${B}Report written to: ${PWD}/$OUT/brief.md${R}"
echo
