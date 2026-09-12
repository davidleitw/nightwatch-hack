#!/usr/bin/env bash
# gate.sh — leader 的 merge loop。確定性的事全在這裡;擱置的交給 merge-loop skill(SKILL.md)。
#
#   bash gate.sh doctor                 gh / jq / gate worktree / 主線都在不在
#   bash gate.sh start                  記 day_start(儀表板的 t=0);沒有 gate worktree 就建
#   bash gate.sh watch [秒]             每 N 秒(預設 90)掃一次,Ctrl-C 停。放一個 terminal 分頁跑一整天
#   bash gate.sh scan                   掃一次就結束
#   bash gate.sh held                   列出擱置中的 PR(給 skill 讀)
#   bash gate.sh show <N>               把 PR 抓進 gate worktree,列出改了哪些檔、截圖在哪(給 skill 看圖用)
#   bash gate.sh merge <N> [原因,原因]  免除這些擱置原因,重驗一次,乾淨就合併
#   bash gate.sh return <N> <一句話>    退回:留言(貼 check log 尾巴 + 這句話),記 returned;對方推新 commit 會自動重驗
#   bash gate.sh env <N> <一句話>       判定是環境的錯不是 PR 的錯:記 env,下一輪重驗
#   bash gate.sh answer <N> <一句話>    契約疑問已回答:記 answered,之後這條 PR 不再因疑問擱置
#   bash gate.sh note <一句話>          leader 隨手記一筆(砍範圍、換模型、環境事故)
#
# 事件寫在 events.jsonl(欄位見 EVENTS.md);有設驗收指令的話輸出在 logs/、codex 回報全文在 reports/。
# 環境變數:GATE_BASE(master) GATE_WORKTREE(<repo>-gate) GATE_PARTS("shop control console")
#           GATE_SHOTS_PARTS(console) GATE_PARTS_ROOT(hackathon) GATE_BY(script|agent|leader)
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
main=$(git -C "$here" rev-parse --show-toplevel)
# repo 根的 hackathon.conf 先讀進來 —— 那裡的 export GATE_* 才會生效。
# 真正的環境變數優先:已經設過的不會被蓋掉(conf 裡是 export X="" 的話仍以 conf 為準)。
[[ -f "$main/hackathon.conf" ]] && . "$main/hackathon.conf"
# 部份目錄沒特別設就跟著 conf 的 PARTS 走
: "${GATE_PARTS:=${PARTS:-shop control console}}"
gate=${GATE_WORKTREE:-"${main}-gate"}
base=${GATE_BASE:-master}
parts=${GATE_PARTS:-"shop control console"}
shots_parts=${GATE_SHOTS_PARTS:-console}
parts_root=${GATE_PARTS_ROOT-hackathon}          # 沒有冒號:GATE_PARTS_ROOT="" 就真的是空的(部份在 repo 根)
# parts_root 可以是空的(部份目錄就在 repo 根)。下面一律用這兩個衍生值,自己接 "/" 會變成絕對路徑。
ppfx="${parts_root:+$parts_root/}"                    # 路徑前綴:hackathon/ 或 空
psub="${parts_root:+/$parts_root}"                    # 接在目錄後面:/hackathon 或 空

# 驗收是選用的。要跑就得同時有 CHECK_CMD 與那個檔 —— 不假設每台機器的環境一樣,
# 沒有就只驗格式、目錄擁有權、能不能合主線、契約疑問。
check_cmd=${GATE_CHECK_CMD-${CHECK_CMD-}}
has_check=false
[[ -n "$check_cmd" && -f "$main$psub/${check_cmd##* }" ]] && has_check=true
events=$here/events.jsonl; logs=$here/logs; reports=$here/reports; state=$here/state
qhead=${GATE_QHEAD:-'契約疑問'}                       # codex 回報裡那一節的標題
qnone=${GATE_QNONE:-'^[[:space:]:：*-]*(無|沒有|none|None|n/a|N/A)?[[:space:]。.*]*$'}   # 「沒有疑問」長這樣
mkdir -p "$logs" "$reports" "$state"

# ---------- 小工具 ----------
say() { printf '%s\n' "$*"; }
die() { say "gate: $*" >&2; exit 2; }
now() { date +%Y-%m-%dT%H:%M:%S%z | sed 's/\(..\)$/:\1/'; }
epoch() { date +%s; }
ghm() { (cd "$main" && gh "$@" </dev/null); }
ghg() { (cd "$gate" && gh "$@" </dev/null); }
short() { printf '%.12s' "$1"; }
leader() {
  if [[ ! -s "$state/leader" ]]; then ghm api user --jq .login >"$state/leader" 2>/dev/null || die "gh 沒登入"; fi
  cat "$state/leader"
}
# gate worktree 只有一個,所有會碰它的動作都要拿鎖(mkdir 是原子的;macOS 沒有 flock)
lock() {
  local i=0
  while ! mkdir "$state/lock" 2>/dev/null; do
    if [[ -f "$state/lock/pid" ]] && ! kill -0 "$(cat "$state/lock/pid")" 2>/dev/null; then rm -rf "$state/lock"; continue; fi
    (( i++ )); (( i > 600 )) && die "等鎖超過 10 分鐘:$state/lock"
    [[ $i -eq 1 ]] && say "gate: 等另一個 gate 動作結束…"
    sleep 1
  done
  echo $$ >"$state/lock/pid"
}
unlock() { rm -rf "$state/lock"; }
in_list() { local x=$1; shift; local y; for y in "$@"; do [[ "$x" == "$y" ]] && return 0; done; return 1; }
csv_to_json() { [[ -z "${1:-}" ]] && { echo '[]'; return; }; printf '%s' "$1" | tr ',' '\n' | jq -R . | jq -sc .; }
lines_to_json() { if [[ -z "${1:-}" ]]; then echo '[]'; else printf '%s\n' "$1" | jq -R . | jq -sc .; fi; }

# ---------- 事件 ----------
# emit <json>:追加一行。一行 < 4KB、單次 write,兩個寫入者(watch 與 skill)不會互相截斷。
emit() { printf '%s\n' "$1" >>"$events"; }
# 這條 PR 之前的最後一筆事件(用來補 sha/part 等欄位)
last_event() { [[ -s "$events" ]] && jq -c --argjson n "$1" 'select(.pr==$n)' "$events" | tail -1; }
attempt_no() { local c=0; [[ -s "$events" ]] && c=$(jq -c --argjson n "$1" 'select(.pr==$n and (.kind=="held" or .kind=="merged"))' "$events" | wc -l | tr -d ' '); echo $((c+1)); }
# 跟進事件:returned / env / answered,由 skill 或 leader 下,沿用 held 那筆的識別欄位
emit_followup() {  # kind pr note
  local prev; prev=$(last_event "$2")
  [[ -n "$prev" ]] || die "events.jsonl 裡沒有 PR #$2,先 scan"
  emit "$(jq -nc --arg ts "$(now)" --arg k "$1" --arg by "${GATE_BY:-agent}" --arg note "$3" --argjson prev "$prev" \
    '{ts:$ts,kind:$k,by:$by,pr:$prev.pr,sha:$prev.sha,branch:$prev.branch,part:$prev.part,nn:$prev.nn,title:$prev.title,author:$prev.author,attempt:$prev.attempt,note:$note}')"
}

# ---------- gate worktree ----------
ensure_gate() {
  if [[ ! -d "$gate/.git" && ! -f "$gate/.git" ]]; then
    say "gate: 建 worktree $gate"
    git -C "$main" fetch --prune origin "$base" >/dev/null 2>&1
    git -C "$main" worktree add --detach "$gate" "origin/$base" || die "建不了 gate worktree"
  fi
}
# 把 PR 的 head 放進 gate worktree,並把主線合進來(驗的是「合併後長什麼樣」)。回 0 乾淨、1 衝突、2 抓不到
checkout_merged() {  # pr head
  git -C "$gate" merge --abort >/dev/null 2>&1 || true
  git -C "$gate" reset -q --hard && git -C "$gate" clean -qfd
  ghg pr checkout "$1" --detach >/dev/null 2>&1 || return 2
  [[ "$(git -C "$gate" rev-parse HEAD)" == "$2" ]] || return 2       # 中間又推了新 commit,下一輪再驗
  git -C "$gate" fetch -q --prune origin "$base" || return 2
  if git -C "$gate" merge --no-edit "origin/$base" >/dev/null 2>&1; then return 0; fi
  git -C "$gate" merge --abort >/dev/null 2>&1; return 1
}
ff_main() {  # 只快轉主線；Git 拒絕覆蓋本機修改，不自動 stash/reset。
  local output
  if ! output=$(git -C "$main" fetch --prune origin "+refs/heads/$base:refs/remotes/origin/$base" 2>&1); then
    say "本機同步失敗(fetch): $output"; return 1
  fi
  if [[ "$(git -C "$main" symbolic-ref --short HEAD 2>/dev/null)" != "$base" ]]; then
    say "已更新 origin/${base}；主目錄不在 ${base}，保留目前分支，未快轉"; return 1
  fi
  if ! output=$(git -C "$main" -c merge.autostash=false merge --ff-only "origin/$base" 2>&1); then
    say "本機 $base 未快轉，保留本機修改與提交: $output"; return 1
  fi
  say "本機 $base 已同步到 $(git -C "$main" rev-parse HEAD)"
}

# ---------- 驗一條 PR ----------
# 輸入:一筆 gh pr list 的 JSON。副作用:寫 logs/ reports/ state/,發事件,乾淨就合併。
verify() {
  local pr=$1
  local n head branch draft mergeable labels author opened title body
  n=$(jq -r .number <<<"$pr"); head=$(jq -r .headRefOid <<<"$pr"); branch=$(jq -r .headRefName <<<"$pr")
  mergeable=$(jq -r .mergeable <<<"$pr"); labels=$(jq -r '[.labels[].name]|join(",")' <<<"$pr")
  author=$(jq -r .author.login <<<"$pr"); opened=$(jq -r .createdAt <<<"$pr"); title=$(jq -r .title <<<"$pr")
  body=$(jq -r '.body // ""' <<<"$pr" | tr -d '\r')
  [[ "$(jq -r .baseRefName <<<"$pr")" == "$base" ]] || die "#$n 的目標不是 $base,不合併"
  [[ "$(jq -r .isDraft <<<"$pr")" == false ]] || die "#$n 是 draft,不合併"
  [[ -z "${GATE_EXPECT_HEAD:-}" || "$head" == "$GATE_EXPECT_HEAD" ]] || die "#$n head 已變,需要重新審查"
  local sha; sha=$(short "$head")
  local marker="$state/$n-$sha.verdict"
  local waived=""
  if [[ -n "${GATE_EXPECT_HEAD:-}" ]]; then
    waived=${GATE_REVIEW_WAIVERS:-}
  elif [[ -f "$state/$n.waive" ]]; then
    waived=$(tr '\n' ',' <"$state/$n.waive" | sed 's/,$//')
  fi
  local reasons="" notes=""
  add_reason() { in_list "$1" $(tr ',' ' ' <<<"$waived") && return; in_list "$1" $(tr ',' ' ' <<<"$reasons") || reasons="${reasons:+$reasons,}$1"; }
  add_note() { notes="${notes:+$notes;}$1"; }

  # 1. 分支名 <part>/<nn>-<name> → 推出部份
  local part="" nn=""
  if [[ "$branch" =~ ^([a-z]+)/([0-9]{2})-[A-Za-z0-9._-]+$ ]]; then part=${BASH_REMATCH[1]}; nn=${BASH_REMATCH[2]}; fi
  if [[ -z "$part" ]] || ! in_list "$part" $parts; then add_reason bad_format; add_note "分支名 $branch 不是 <部份>/<NN>-<短名>"; fi
  # 2. 標籤 part:<part>(有才比,沒有不擋)
  if [[ -n "$part" && -n "$labels" ]]; then
    local lp; lp=$(tr ',' '\n' <<<"$labels" | sed -n 's/^part://p' | head -1)
    if [[ -n "$lp" && "$lp" != "$part" ]]; then add_reason bad_format; add_note "標籤 part:$lp 跟分支 $part 不合"; fi
  fi
  # 3. 首行註解 <!-- nightwatch part= nn= check= secs= files= -->
  local ann self_check=null self_secs=null self_files=null self_part=""
  ann=$(printf '%s\n' "$body" | head -3 | grep -F 'nightwatch' | head -1 || true)
  if [[ -n "$ann" ]]; then
    [[ "$ann" =~ part=([a-z]+) ]] && self_part=${BASH_REMATCH[1]}
    [[ "$ann" =~ check=([a-z]+) ]] && self_check="\"${BASH_REMATCH[1]}\""
    [[ "$ann" =~ secs=([0-9]+) ]] && self_secs=${BASH_REMATCH[1]}
    [[ "$ann" =~ files=([0-9]+) ]] && self_files=${BASH_REMATCH[1]}
    if [[ -n "$part" && -n "$self_part" && "$self_part" != "$part" ]]; then add_reason bad_format; add_note "註解 part=$self_part 跟分支 $part 不合"; fi
  else add_reason bad_format; add_note "PR 內文第一行沒有 nightwatch 註解"; fi
  # 4. 只碰自己的目錄
  local files outside="" nfiles=0
  files=$(ghm pr diff "$n" --name-only) || die "#$n 讀取檔案差異失敗,不合併"
  nfiles=$(printf '%s\n' "$files" | sed '/^$/d' | wc -l | tr -d ' ')
  if [[ -n "$part" ]]; then
    outside=$(printf '%s\n' "$files" | sed '/^$/d' | grep -v "^${ppfx}$part/" || true)
    [[ -n "$outside" ]] && { add_reason outside_dir; add_note "碰到:$(paste -sd' ' - <<<"$outside")"; }
  else
    outside=$files
    [[ -n "$outside" ]] && { add_reason outside_dir; add_note "非任務分支,需逐筆審查改動範圍"; }
  fi
  # 5. 契約疑問
  local questions
  local report_json
  report_json=$(printf '%s' "$body" | python3 -B "$here/report.py") || die "#$n 回報解析失敗,不合併"
  questions=$(jq -r '.latest["契約疑問"] // ""' <<<"$report_json")
  if [[ "${GATE_CONTENT_REVIEW:-0}" != 1 && "$(jq '.missing_sections|length' <<<"$report_json")" -gt 0 ]]; then
    # 缺漏四節不能由分支格式例外放行。
    die "#$n 最新回報缺少四節:$(jq -r '.missing_sections|join("、")' <<<"$report_json")"
  fi
  if [[ -n "$questions" ]] && ! [[ "$questions" =~ $qnone ]]; then add_reason contract_question; else questions=""; fi
  # 6. 前端要有截圖證據(diff 裡有圖,或內文貼了圖)
  local shots=false
  if in_list "$part" $shots_parts; then
    local png; png=$(printf '%s\n' "$files" | grep -Ei '\.(png|jpe?g|webp)$' || true)
    if [[ -n "$png" ]] || [[ "$body" == *'!['* ]] || [[ "$body" == *'.png'* ]]; then shots=true; else add_reason no_screenshot; fi
  fi
  # 7. 衝突 / 還在算
  if [[ "$mergeable" == "UNKNOWN" ]]; then say "  #$n $branch:GitHub 還在算 mergeable,下一輪"; return 0; fi
  [[ "$mergeable" == "CONFLICTING" ]] && add_reason conflict
  # 8. 抓下來、合主線、跑驗收(有設定才跑)
  local check_ran=false check_pass=false check_secs=0 failed="" log="" head_at=null head_epoch="" base_sha=""
  local rc; checkout_merged "$n" "$head"; rc=$?
  if [[ $rc -eq 2 ]]; then say "  #$n $branch:抓不到 head $sha(可能剛推了新 commit),下一輪"; return 0; fi
  head_at=$(git -C "$gate" log -1 --format=%cI "$head"); head_epoch=$(git -C "$gate" log -1 --format=%ct "$head")
  base_sha=$(short "$(git -C "$gate" rev-parse "origin/$base")")
  [[ -z "${GATE_EXPECT_BASE:-}" || "$(git -C "$gate" rev-parse "origin/$base")" == "$GATE_EXPECT_BASE" ]] || die "master 已變,需要重新審查 #$n"
  if [[ $rc -eq 1 ]]; then add_reason conflict; add_note "跟 origin/$base 合不起來"; fi
  if ! in_list conflict $(tr ',' ' ' <<<"$reasons") && [[ -n "$part" ]] && $has_check; then
    log="logs/$n-$sha.log"; check_ran=true
    local t0=$SECONDS
    (cd "$gate$psub" && perl -e 'alarm shift; exec @ARGV' 600 $check_cmd "$part") >"$here/$log" 2>&1
    local crc=$?; check_secs=$((SECONDS-t0))
    if [[ $crc -eq 0 ]]; then check_pass=true; else add_reason check_red; failed=$(grep '✘' "$here/$log" | sed 's/^[[:space:]]*✘ //' || true); fi
    [[ $crc -eq 142 ]] && add_note "驗收超過 10 分鐘被砍"
    [[ $crc -ne 0 && -z "$failed" ]] && add_note "驗收 exit $crc 但沒有 ✘ 行,看 log"

  fi
  printf '%s\n' "$body" >"$reports/$n-$sha.md"
  [[ -n "${GATE_REVIEW_NOTE:-}" ]] && add_note "$GATE_REVIEW_NOTE"
  # 9. 判定
  local kind=held; [[ -z "$reasons" ]] && kind=merged
  local merge_sha=null
  if [[ $kind == merged ]]; then
    if ghm pr merge "$n" --squash --match-head-commit "$head" >"$state/merge.out" 2>&1; then
      local merged_pr
      merged_pr=$(ghm pr view "$n" --json state,mergeCommit) || die "#$n 無法確認合併結果,不寫 merged"
      if [[ "$(jq -r .state <<<"$merged_pr")" == MERGED ]]; then
        merge_sha=$(jq -r '.mergeCommit.oid // ""' <<<"$merged_pr")
        [[ -n "$merge_sha" ]] && merge_sha="\"$(short "$merge_sha")\"" || merge_sha=null
      else
        kind=held; reasons=merge_failed; add_note "GitHub 尚未合併(可能等待 checks 或 merge queue)"
      fi
    else
      kind=held; reasons=merge_failed; add_note "gh pr merge 失敗:$(tail -1 "$state/merge.out")"
    fi
  fi
  if [[ $kind == merged ]]; then
    local sync_note
    sync_note=$(ff_main)
    add_note "$sync_note"
    say "$sync_note"
  fi
  local ev; ev=$(jq -nc \
    --arg ts "$(now)" --arg kind "$kind" --arg by "${GATE_BY:-script}" --argjson pr "$n" --arg sha "$sha" \
    --arg branch "$branch" --arg part "$part" --arg nn "$nn" --arg title "$title" --arg author "$author" \
    --argjson attempt "$(attempt_no "$n")" --arg opened_at "$opened" --arg head_at "$head_at" \
    --argjson wait_secs "$(( $(epoch) - ${head_epoch:-$(epoch)} ))" --argjson files "$nfiles" --argjson outside "$(lines_to_json "$outside")" \
    --argjson self "$(jq -nc --argjson c "$self_check" --argjson s "$self_secs" --argjson f "$self_files" '{check:$c,secs:$s,files:$f}')" \
    --argjson check "$(jq -nc --argjson ran "$check_ran" --argjson pass "$check_pass" --argjson secs "$check_secs" --argjson failed "$(lines_to_json "$failed")" --arg log "$log" '{ran:$ran,pass:$pass,secs:$secs,failed:$failed,log:$log}')" \
    --argjson reasons "$(csv_to_json "$reasons")" --argjson waived "$(csv_to_json "$waived")" \
    --arg questions "$questions" --argjson shots "$shots" --arg mergeable "$mergeable" --arg base_sha "$base_sha" \
    --argjson merge_sha "$merge_sha" --arg report "reports/$n-$sha.md" --arg note "$notes" \
    '{ts:$ts,kind:$kind,by:$by,pr:$pr,sha:$sha,branch:$branch,part:$part,nn:$nn,title:$title,author:$author,attempt:$attempt,opened_at:$opened_at,head_at:$head_at,wait_secs:$wait_secs,files:$files,outside:$outside,self:$self,check:$check,reasons:$reasons,waived:$waived,questions:$questions,shots:$shots,mergeable:$mergeable,base_sha:$base_sha,merge_sha:$merge_sha,report:$report,note:$note}')
  if [[ -z "$ev" ]]; then   # 事件建不出來也不能讓已經發生的合併沒有紀錄
    ev=$(jq -nc --arg ts "$(now)" --arg kind "$kind" --argjson pr "$n" --arg sha "$sha" --arg branch "$branch" --arg part "$part" --arg nn "$nn" --arg r "$reasons" '{ts:$ts,kind:$kind,by:"script",pr:$pr,sha:$sha,branch:$branch,part:$part,nn:$nn,attempt:1,reasons:($r|split(",")|map(select(.!=""))),note:"事件欄位建構失敗,只有最小紀錄"}')
    say "  gate: #$n 事件欄位建構失敗,寫了最小紀錄" >&2
  fi
  emit "$ev"; echo "$kind" >"$marker"
  if [[ $kind == merged ]]; then
    say "  #$n $branch:merged $(jq -r .merge_sha <<<"$ev")(check ${check_secs}s,第 $(jq -r .attempt <<<"$ev") 次)"
  else
    say "  #$n $branch:held [$reasons] $notes"
    local tailtxt=""; [[ -n "$log" && $check_pass == false && $check_ran == true ]] && tailtxt=$(printf '\n\n<details><summary>驗收輸出尾 40 行</summary>\n\n```\n%s\n```\n</details>' "$(tail -40 "$here/$log")")
    if [[ "${GATE_NO_COMMENTS:-0}" != 1 ]]; then
      ghm pr comment "$n" --body "$(printf 'gate 擱置(head %s,第 %s 次):**%s**\n%s%s\n\nleader 會看;推新 commit 會自動重驗。' "$sha" "$(jq -r .attempt <<<"$ev")" "$reasons" "$notes" "$tailtxt")" >/dev/null 2>&1 || say "  (留言失敗)"
    fi
  fi
}

scan() {
  ensure_gate
  local me; me=$(leader)
  local prs; prs=$(ghm pr list --state open --base "$base" --limit 50 --json number,headRefName,headRefOid,isDraft,mergeable,labels,author,createdAt,title,body,baseRefName) || { say "gate: gh pr list 失敗"; return 1; }
  printf '%s' "$prs" >"$state/prs.json"; now >"$state/heartbeat"
  say "== scan $(now)  open PR:$(jq length <<<"$prs")"
  local pr
  while IFS= read -r pr; do
    [[ -z "$pr" ]] && continue
    local n sha
    n=$(jq -r .number <<<"$pr"); sha=$(short "$(jq -r .headRefOid <<<"$pr")")
    if [[ "$(jq -r .isDraft <<<"$pr")" == true ]]; then say "  #$n:draft,跳過"; continue; fi
    if [[ "$(jq -r .author.login <<<"$pr")" == "$me" ]]; then say "  #$n:leader 自己的,跳過"; continue; fi
    if [[ -f "$state/$n-$sha.verdict" ]]; then say "  #$n $(jq -r .headRefName <<<"$pr") $sha:$(cat "$state/$n-$sha.verdict"),等對方"; continue; fi
    verify "$pr"
  done < <(jq -c '.[]' <<<"$prs")
}

held() {
  [[ -s "$events" ]] || { say "沒有事件"; return; }
  local open; open=$(ghm pr list --state open --limit 50 --json number --jq '[.[].number]' 2>/dev/null || echo '[]')
  jq -r --argjson open "$open" -s '
    group_by(.pr) | map(last) | map(select(.kind=="held" and (.pr as $p | $open | index($p)))) | .[]
    | "#\(.pr) \(.branch) \(.sha) 第\(.attempt)次 reasons=\(.reasons|join(",")) failed=\(.check.failed|join(" | ")) log=\(.check.log) report=\(.report) waived=\(.waived|join(","))\(if .questions!="" then "\n   疑問:" + .questions else "" end)\(if .note!="" then "\n   note:" + .note else "" end)"
  ' "$events"
  say "(空的就是沒有擱置)"
}

show() {
  ensure_gate; lock; trap unlock EXIT
  local head; head=$(ghm pr view "$1" --json headRefOid --jq .headRefOid) || die "找不到 PR #$1"
  local rc; checkout_merged "$1" "$head"; rc=$?
  say "gate worktree:$gate(head $(short "$head"),合主線:$([[ $rc -eq 0 ]] && echo 乾淨 || echo 衝突))"
  say "改的檔:"; ghm pr diff "$1" --name-only | sed 's/^/  /'
  say "圖:"; ghm pr diff "$1" --name-only | grep -Ei '\.(png|jpe?g|webp)$' | sed "s|^|  $gate/|" || say "  (diff 裡沒有)"
}

usage() { sed -n '2,18p' "$0"; exit 1; }
cmd=${1:-}; shift || true
case "$cmd" in
  doctor)
    ok=0
    command -v gh >/dev/null && say "gh   ok $(gh --version | head -1)" || { say "gh   缺"; ok=1; }
    command -v jq >/dev/null && say "jq   ok" || { say "jq   缺(brew install jq)"; ok=1; }
    ghm auth status >/dev/null 2>&1 && say "auth ok $(leader)" || { say "auth 沒登入"; ok=1; }
    git -C "$main" ls-remote --exit-code --heads origin "$base" >/dev/null 2>&1 && say "base ok origin/$base" || { say "base 找不到 origin/$base"; ok=1; }
    [[ -e "$gate/.git" ]] && say "gate ok $gate" || say "gate 還沒有($gate),start 會建"
    if ! $has_check; then say "check 沒設定,PR 只驗格式/目錄/合併/契約疑問"
    else say "check ok ${ppfx}${check_cmd##* }"; fi
    exit $ok ;;
  sync)
    lock; ff_main; exit $? ;;
  start)
    ensure_gate
    emit "$(jq -nc --arg ts "$(now)" --arg by "${GATE_BY:-leader}" --arg base "$base" --arg parts "$parts" '{ts:$ts,kind:"day_start",by:$by,base:$base,parts:($parts|split(" "))}')"
    say "day_start $(now) 記進 $events" ;;
  watch)
    every=${1:-90}; ensure_gate; trap 'unlock; exit 0' INT TERM
    while :; do lock; scan; unlock; sleep "$every"; done ;;
  scan)   lock; trap unlock EXIT; scan ;;
  held)   held ;;
  show)   [[ -n "${1:-}" ]] || usage; show "$1" ;;
  merge)
    [[ -n "${1:-}" ]] || usage
    n=$1; w=${2:-}
    lock; trap unlock EXIT
    ensure_gate
    [[ -n "$w" ]] && { tr ',' '\n' <<<"$w" >>"$state/$n.waive"; sort -u -o "$state/$n.waive" "$state/$n.waive"; }
    rm -f "$state/$n-"*.verdict
    pr=$(ghm pr view "$n" --json number,headRefName,headRefOid,isDraft,mergeable,labels,author,createdAt,title,body,baseRefName) || die "找不到 PR #$n"
    export GATE_BY=${GATE_BY:-agent}; verify "$pr" ;;
  return)
    [[ -n "${1:-}" && -n "${2:-}" ]] || usage
    prev=$(last_event "$1"); [[ -n "$prev" ]] || die "events.jsonl 裡沒有 PR #$1"
    log=$(jq -r '.check.log // ""' <<<"$prev"); sha=$(jq -r .sha <<<"$prev")
    tailtxt=""; [[ -n "$log" && -f "$here/$log" ]] && tailtxt=$(printf '\n\n```\n%s\n```' "$(tail -40 "$here/$log")")
    ghm pr comment "$1" --body "$(printf '退回(head %s):%s%s\n\n修好推新 commit 會自動重驗。' "$sha" "$2" "$tailtxt")" >/dev/null || die "留言失敗"
    emit_followup returned "$1" "$2"; echo returned >"$state/$1-$sha.verdict"
    say "#$1 returned:$2" ;;
  env)
    [[ -n "${1:-}" && -n "${2:-}" ]] || usage
    emit_followup env "$1" "$2"; rm -f "$state/$1-"*.verdict
    say "#$1 env:$2(下一輪重驗)" ;;
  answer)
    [[ -n "${1:-}" && -n "${2:-}" ]] || usage
    ghm pr comment "$1" --body "$(printf '契約回答:%s' "$2")" >/dev/null || die "留言失敗"
    echo contract_question >>"$state/$1.waive"; sort -u -o "$state/$1.waive" "$state/$1.waive"
    emit_followup answered "$1" "$2"; rm -f "$state/$1-"*.verdict
    say "#$1 answered:$2(下一輪重驗,不再因疑問擱置)" ;;
  note)
    [[ -n "${1:-}" ]] || usage
    emit "$(jq -nc --arg ts "$(now)" --arg by "${GATE_BY:-leader}" --arg note "$1" '{ts:$ts,kind:"note",by:$by,note:$note}')"; say "記了" ;;
  *) usage ;;
esac
