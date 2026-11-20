#!/usr/bin/env bash
# End-to-end walkthrough of all three agents with fictional data.
# Usage: KEY=aie_... BASE=http://localhost:8000 ./examples/curl_examples.sh
set -euo pipefail
BASE="${BASE:-http://localhost:8000}"
: "${KEY:?Set KEY to an engine API key (aie_...)}"
DIR="$(cd "$(dirname "$0")" && pwd)"
AUTH=(-H "Authorization: Bearer ${KEY}")
JSON=(-H "Content-Type: application/json")
pp() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k: d[k] for k in ("status","mode","language","usage","sources","warnings") if k in d} if "status" in d else d, ensure_ascii=False, indent=2)); print((d.get("response") or "")[:600])'; }

echo "== health";      curl -s "$BASE/health"; echo
echo "== marketing ru"; curl -s "${AUTH[@]}" "${JSON[@]}" "$BASE/agents/marketing/run" \
  -d '{"message":"Создай контент-план для кофейни в Алматы","language":"ru","file_ids":[]}' | pp
echo "== marketing kk"; curl -s "${AUTH[@]}" "${JSON[@]}" "$BASE/agents/marketing/run" \
  -d '{"message":"Алматыдағы кофеханаға арналған контент-жоспар жасап беріңіз"}' | pp

echo "== upload knowledge base"
KB_ID=$(curl -s "${AUTH[@]}" "$BASE/files/upload" -F "file=@${DIR}/files/kb_coffee_ru.txt" -F purpose=knowledge_base \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "kb file: $KB_ID"
echo "== support (answer)";     curl -s "${AUTH[@]}" "${JSON[@]}" "$BASE/agents/support/run" -d '{"message":"Сколько стоит доставка?"}' | pp
echo "== support (escalation)"; curl -s "${AUTH[@]}" "${JSON[@]}" "$BASE/agents/support/run" -d '{"message":"Есть ли у вас парковка?"}' | pp

echo "== upload fictional resume"
CV_ID=$(curl -s "${AUTH[@]}" "$BASE/files/upload" -F "file=@${DIR}/files/resume_fictional_ru.docx" -F purpose=resume \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "== hr summary"; curl -s "${AUTH[@]}" "${JSON[@]}" "$BASE/agents/hr/run" \
  -d "{\"message\":\"Сделай сводку резюме\",\"file_ids\":[\"$CV_ID\"]}" | pp
echo "== hr blocked"; curl -s "${AUTH[@]}" "${JSON[@]}" "$BASE/agents/hr/run" -d '{"message":"Ранжируй кандидатов"}' | pp

echo "== cleanup"
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE "${AUTH[@]}" "$BASE/files/$KB_ID"
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE "${AUTH[@]}" "$BASE/files/$CV_ID"
echo "== stats"; curl -s "${AUTH[@]}" "$BASE/stats/agents"; echo
