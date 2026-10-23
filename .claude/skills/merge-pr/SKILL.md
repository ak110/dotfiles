---
name: merge-pr
user-invocable: false
description: 「PRをマージして」などの明示依頼を受領したとき、対象PRを検査し、マージ後のbranch同期、CIおよび必要なRelease検収を完遂する
---

# PRマージ完遂

このスキルは、明示的なPRマージ依頼を受領した場合と、`agent-toolkit:process-wi`の終端で日次リリースの条件（`dotfiles-release`「developとmasterのリリース運用」）が成立した場合だけ実行する。PRが存在するという観測は起動の契機に含めない。
リリースの成否はリモートのbranch、CI、必要なReleaseで判定し、ローカルの作業ツリーとbranchはユーザーの変更を壊さない範囲でだけ扱う。
`gh`の受理形式と取得可能な項目は、各操作の直前にヘルプで確かめる。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-release/SKILL.md#「developとmasterのリリース運用」` | 判断 | `section:*/skills/dotfiles-release/SKILL.md#「developとmasterのリリース運用」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| CIまたはReleaseのrunが失敗し、原因を分類する前 | `agent-toolkit:bugfix`の`references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/ci-failure-handling.md` | |
| マージ後に到着したレビューを取得して判定する前 | `agent-toolkit/skills/process-wi/references/github-copilot-review-audit.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/github-copilot-review-audit.md` | |

## 失敗時の扱い

- CIとRelease以外の工程が失敗した場合は、成立済みの外部状態を保持し、失敗工程、外部状態、run URL、再開点を報告して停止する。PR作成・マージの再試行、auto-merge、自動修復、自動rollbackは行わない
- CIとReleaseのrunが失敗した場合は、run全体の終端と失敗ログを確認し、`agent-toolkit:bugfix`の`references/ci-failure-handling.md`「再現性」に従って原因を分類する。外部一時要因（ネットワーク断、配布元のタイムアウト、レート制限、ランナー障害など）を疑える場合は、同書の広域障害確認を済ませてから、同じ原因につき失敗jobを1回だけ再実行して検収を続ける
- 再失敗した場合、初回ログで外部一時要因を疑えない場合、ログを取得できない場合は、元のrunの失敗を保持して前記と同じ内容を報告し停止する

## 対象の選択

PR番号またはURLが指定されればそれを対象にする。指定が無ければ`develop`から`master`へのopen PRが1件の場合だけ対象とし、0件や複数件ならPR番号かURLの指定を求めて停止する。headが`develop`以外、baseが`master`以外の対象は状態を変更せず停止する。
GitHubの設定でhead branchを`develop`へ制限する操作は対象外とし、現在の設定を維持する。

## ローカルdevelop同期条件

ユーザーの未コミット変更とローカルbranchを壊さないため、ローカル`develop`は次の全てが成立する場合だけ同期する（基準refは参照する節が指定する）。

- `git worktree list --porcelain`で`branch refs/heads/develop`を持つworktreeが1件だけある（その絶対パスを`develop` worktreeとする）
- そのworktreeの`git status --porcelain`が空で、現在branchが`develop`である
- rebase・merge・cherry-pickの中断状態が無い（対象worktreeのGitディレクトリの`rebase-merge`、`rebase-apply`、`MERGE_HEAD`、`CHERRY_PICK_HEAD`の不在）
- `git merge-base --is-ancestor HEAD <基準ref>`が0を返す（fast-forwardできる）

## マージ前の確認

- `git fetch origin develop master`の後、`origin/develop`と、PR番号から取得した`headRefOid`が同じcommitであることを確かめ、この`headRefOid`を確認対象として保持する
- PRはopenかつdraftでなく、baseが`master`、headが`develop`で、mergeableであること。マージの前提はこれらのリモート側の条件だけとし、ローカルの状態は含めない
- ローカル`develop`を後で同期するかは、基準refを`origin/develop`とした「ローカルdevelop同期条件」で見込みを判定する。不成立ならローカルには触れずリモートだけで完遂する
- 必須checkを待つ前に、同じPRに`event=pull_request`、`workflow=CI`、`name=statusline-version`のcheckが登録されるまで待つ。待機は`gh pr checks`のJSON出力の上限付きの反復取得で行う。同じheadの`push`起点の同名checkは数えない。`push`起点の成功後に`pull_request`起点の`statusline-version`が非同期に登録されることがあるためである。観測と再検証手順は`docs/development/audit-records.md`「.claude/skills/merge-pr/SKILL.md：マージ前の確認：2026年10月5日」にある
- 登録後に`gh pr checks --required --watch`で必須checkの終端まで待ち、成功後は`headRefOid`が保持したheadと一致したまま`mergeStateStatus=CLEAN`になるまで上限付きで待つ
- 各照会でheadが変わったら停止する。上限到達、照会失敗、対象の曖昧さ、check失敗、head変更、`CLEAN`以外の状態では、マージせずに止める。必須checkの失敗は「失敗時の扱い」、それ以外の停止は外部状態と再開点を報告する

## レビューコメントの確認

必須checkの待機と並行して、対象PRのレビューと行コメントを取得し、各指摘の妥当性を実装と規範を読んで判定する。

- 成立する指摘は`agent-toolkit/rules/01-agent.md`「完遂と先送り」で同一セッションで対応するものと次セッション以降へ回すものに分け、後者だけをAWIへ登録する。成立しない指摘は登録せず、根拠を報告へ残す
- 全指摘の判定と処置を終えてからマージへ進む。同一セッションで是正した場合は、修正後のPR headを新たな確認対象として「マージ前の確認」をやり直す（修正前の必須check成功は根拠にしない）

## PRのマージ

マージの直前に`headRefOid`と`mergeStateStatus`を再取得し、確認対象のcommitと一致し`CLEAN`であることを確かめる（一致しなければ停止して報告する）。
マージは操作直前のhead OIDを原子的な一致条件として渡し、明示的なマージコミットを作成する。自動マージとbranch削除は指定せずに実行する。この条件を`gh`で保証できない場合はマージを見送る。マージコマンドは1回だけ実行し、失敗したら再試行せず理由と再開点を報告する。

## マージ後のbranch同期とCI

- `git fetch origin master`の後、PRの`mergeCommit.oid`と`origin/master`が一致することを確かめる。以降のGit操作は`origin/master`を基準にする
- `origin/develop`はローカルbranchを操作元にせず、`git push origin origin/master:refs/heads/develop`で更新する。fetch後に`origin/develop`と`origin/master`の短縮OID（7文字以上）が一致することを確かめる
- ローカル`develop`の同期を見込んだ場合は、実行直前に基準refを`origin/master`とした「ローカルdevelop同期条件」を再取得し、`develop` worktreeがマージ前と同じパスであることも確かめる。成立すれば`git -C <develop worktreeの絶対パス> merge --ff-only origin/master`を実行し、`origin/master`との一致を確かめる。条件が崩れていれば同期だけを省き、省いた条件、対象worktreeの絶対パス、ローカル`develop`と`origin/master`の短縮OIDを完了報告へ記録する
- マージ後のmaster pushと同期後のdevelop pushのCIは、必須checkを通過したPR headと同じツリーの再確認になるため待たない（努力目標）。statuslineの変更を含む場合のmaster CIの結論は、`Release statusLine`（master pushのCI成功を契機に起動）のrunの成否で「条件付きRelease検収」が確かめる

## 条件付きRelease検収

マージコミットの第一親との差分に`rust/claude-statusline/`の変更が無ければ省く。ある場合は次を確かめる。

- `origin/master`の完全OIDに対応する`Release statusLine` runを完全なdatabase IDで特定し、`prepare`が省略されずに実行されたrunを終端まで待つ。`gate`以外のジョブが全て省略されたrunは対象から外して次のrunを待ち、`prepare`が未確定なら確定まで観測する。master CIの失敗でrunが作成されない場合は「失敗時の扱い」に従う
- manifestの版数に対応する`statusline-v<version>` tagが`origin/master`を指すこと
- GitHub Releaseが存在し、`pytools/_internal/setup_statusline_binary.py`の`ASSET_NAME_POSIX`と`ASSET_NAME_WINDOWS`の名前のassetがあること

同じ`origin/master`の完全OIDに対応する結果だけを検収する。Release runの失敗は「失敗時の扱い」を適用し、tag、Release、assetの検収失敗は外部状態、失敗工程、run URL、再開点を報告する。

## マージ後に到着したレビューの確認

GitHub Copilotのレビューはマージ後に到着することがあるため、前2節を終えた時点で1回だけ確認する。

- `atk review-audit pending --repo <OWNER>/<REPO>`が終了コード0でJSONの`reviews`と`threads`を解釈でき、対象PRがどちらにも含まれなければ取得を省く。それ以外（含まれる、非0、解釈不能）は対象PRのCopilot由来のreview本文とreview threadを1回取得する
- 取得、判定、GitHubへの記録、判定済みの記録は`agent-toolkit/skills/process-wi/references/github-copilot-review-audit.md`に従い、対象をそのPRへ限定する。新しいレビューの要求や到着の待機はしない（未到着分は`agent-toolkit:process-wi`の選定工程の監査が次回以降に取得する）
- 要修正の指摘は「レビューコメントの確認」の振り分けに従う。同一セッションで是正する場合は`develop`への通常の変更として扱い、マージ工程を再実行せず次回のリリースPRへ公開を委ねる

## マージ後に`develop`へ加えた変更のCI確認

`agent-toolkit:process-wi`の終端から本スキルを実行した場合に限り、マージ完遂後に同じセッションで`develop`へ加えた変更は、pushしてCI runが起動した時点で公開工程を終え、CIの完了待ちを省く。次回のリリースPRが必須checkを待ち、`master`は必須CIを通過したマージコミットだけで更新されるため、未検証の変更は`develop`に留まる。変更範囲の検証は通常どおり成功させてからpushし、省いたCIのrun URLを完了報告へ残す。本スキルのマージ工程自体のCI検収は前掲の各節のとおりとする。

## 完了条件

- マージ後のbranch同期時に`origin/develop`と`origin/master`の短縮OIDの一致を確認し、マージ対象の必須CIと必要なRelease検収が成功している
- 「マージ後に到着したレビューの確認」を1回実施し、指摘の分類と処置を確定している
- ローカル`develop`を同期した場合は、その同期時に`origin/master`との一致を確認している

マージ後に同じセッションで`develop`へ加えた変更は、「マージ後に`develop`へ加えた変更のCI確認」の条件で判定し、検収済みのマージ結果と分けて報告する。

ローカルの作業ツリーとbranchの状態は合否判定に使わない。`git status --short`の出力は現状の情報として完了報告へ添え、リモートの完了とローカル`develop`を同期したかどうかを区別して示す。
