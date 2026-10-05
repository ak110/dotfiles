---
name: merge-pr
user-invocable: false
description: 「PRをマージして」などの明示依頼を受領したとき、対象PRを検査し、マージ後のbranch同期、CIおよび必要なRelease検収を完遂する
---

# PRマージ完遂

このスキルは、明示的なPRマージ依頼を受領した場合と、`agent-toolkit:process-wi`の終端で日次リリースの条件が成立した場合だけ実行する。
条件は`dotfiles-release`スキルの「developとmasterのリリース運用」が定める。
PRが存在するという観測を起動の契機から外し、前記2つの場合だけ起動する。

## 失敗時の共通規定

CIとRelease以外の工程が失敗した場合は、成立済みの外部状態を保持し、失敗した工程、外部状態、run URLおよび再開点を報告して停止する。PR作成・マージ操作の再試行とauto-merge、自動修復、自動rollbackは行わない。

CIとReleaseのrunが失敗した場合は、run全体の終端と失敗ログを確認し、`agent-toolkit:bugfix`の`references/ci-failure-handling.md`「再現性」に従って原因を分類する。ネットワーク断、配布元のタイムアウト、レート制限、ランナー障害など、ログで外部一時要因を疑える場合は、同書の広域障害確認を済ませてから、同じ原因につき失敗jobを一度だけ再実行する。

runの失敗ログ取得、失敗jobの再実行、run全体の終端観測には`gh`を使い、各操作の受理形式は実行直前のヘルプで確定する。

再実行後のログと結果で検収を続ける。再失敗、または初回ログで外部一時要因を疑えない場合は、成立済みの外部状態、失敗工程、run URLおよび再開点を報告して停止する。ログを取得できない場合も、元のrunの失敗を保持して停止する。

## 対象の選択

PR番号またはPR URLが指定された場合は、その対象を読み取る。
引数がない場合は、`develop`から`master`へのopen PRを一覧し、1件だけなら対象にする。
該当するPRが0件のときと複数件のときは、PR番号かURLの指定を求めて状態を変更せず停止する。
明示対象のheadが`develop`以外、baseが`master`以外の場合は、状態を変更せず停止する。

対象の確認には`gh`でPRの番号、URL、状態、draft、merge可否、headとbaseのbranch・OID、merge commitを取得する。受理形式と取得可能な項目は実行直前のヘルプで確定する。

GitHubの設定でhead branchを`develop`だけに制限する操作は行わず、現在の設定のまま維持する。

## ローカルdevelop同期条件

ユーザーの未コミット変更とローカルbranchを壊さない場合だけ、ローカル`develop`を同期する。
次の全てが成立することを「ローカルdevelop同期条件」と呼ぶ。基準refは参照する節が指定する。

- `git worktree list --porcelain`の出力で`branch refs/heads/develop`を持つblockが1件だけある。その`worktree`行の絶対パスを`develop` worktreeとする
- `develop` worktreeの`git status --porcelain`の出力が空である
- 現在branchが`develop`である
- rebase・merge・cherry-pickの中断状態が無い。中断状態は対象worktreeに対応するGitディレクトリ（`--git-dir`）の`rebase-merge`、`rebase-apply`、`MERGE_HEAD`と`CHERRY_PICK_HEAD`の実在で判定する
- `git merge-base --is-ancestor HEAD <基準ref>`が終了コード0を返す（fast-forwardが成立する）

観測には次の読み取りコマンドを使う。

```sh
git worktree list --porcelain
git -C <develop worktreeの絶対パス> status --porcelain
git -C <develop worktreeの絶対パス> rev-parse --abbrev-ref HEAD
git -C <develop worktreeの絶対パス> rev-parse --short=7 <基準ref>
git -C <develop worktreeの絶対パス> merge-base --is-ancestor HEAD <基準ref>
```

## マージ前の確認

`git fetch origin develop master`でremote-tracking refを更新し、`origin/develop`とPR番号から操作直前に取得した`headRefOid`が同じcommitを指すことを確認する。この`headRefOid`を確認対象として保持する。
PRはopenかつdraftでなく、baseが`master`、headが`develop`で、mergeableが成立していなければならない。
マージの前提と続行の判定はこれらのリモート側の条件だけで行い、作業ツリーのclean、現在branchおよびローカル`develop`の位置はこの前提から外す。

ローカル`develop`を同期するかどうかは、マージの前提とは分けて、基準refを`origin/develop`とした「ローカルdevelop同期条件」で判定する。
成立する場合は`develop` worktreeの絶対パスを保持し、マージ後にそのworktreeでローカル`develop`の同期を試みる。
成立しない場合は、対象worktreeとローカル`develop`に加え、既存の未コミット差分も変更せず保持する。リモートだけでリリースを完遂する。
この判定はマージ前時点の見込みであり、同期を実行してよいかはマージ後に同じ条件を再取得して確定する。

最初に`gh pr checks`のJSON出力を上限付きで反復取得し、同じPRに`event=pull_request`、`workflow=CI`、`name=statusline-version`のcheckが登録されるまで待つ。JSON項目は`event`、`workflow`、`name`を使い、実行直前のヘルプで受理形式を確定する。登録前に存在する同じhead・同名の`push`起点checkは登録完了に数えない。各照会後にPRの`headRefOid`を再取得し、保持したheadから変化した場合は停止する。

PR起点checkの登録後だけ、`gh pr checks --required --watch`で必須checkの終端まで待つ。待機と必須checkの指定形式は実行直前のヘルプで確定する。

必須check成功後はPRの`headRefOid`と`mergeStateStatus`を上限付きで再取得し、保持したheadと一致したまま`mergeStateStatus=CLEAN`になるまで待つ。GitHubのマージ可否評価が`CLEAN`となった場合だけ「PRのマージ」へ進む。

登録待機または`CLEAN`待機の上限到達、照会失敗、対象の曖昧さ、check失敗、head変更および`CLEAN`以外の状態ではマージしない。必須checkの失敗は該当runの終端とログを確認して「失敗時の共通規定」を適用する。その他の停止では、観測した外部状態と再開点を報告する。

PR #138では、同じheadの`push`起点check成功後に`pull_request`起点の`statusline-version`が非同期に登録された。観測版と再検証手順は`docs/development/audit-records.md`の「.claude/skills/merge-pr/SKILL.md：マージ前の確認：2026年10月5日」を参照する。

## レビューコメントの確認

必須checkの待機と並行して、対象PRのレビューコメントを取得する。

レビューと行コメントを、対象PR番号へ対応付けてGitHubから取得する。`gh`のAPI呼出形式は実行直前のヘルプで確定する。

各指摘は対象の実装と規範を読んで妥当性を判定する。
成立する指摘は`agent-toolkit/rules/01-agent.md`「完遂と先送り」の判定を適用し、
同一セッションで対応する指摘と次セッション以降へ回す指摘へ分ける。
次セッション以降へ回す指摘だけをAWIへ登録する。
成立しない指摘は登録せず、判定の根拠を報告へ残す。
全指摘の判定、必要な同一セッションの是正およびAWI登録を完了してからマージへ進む。
同一セッションで是正した場合は、PR番号から修正後のPR headを再取得し、新たな確認対象として「マージ前の確認」を再実行する。
検収は修正後のPR headに対する必須check成功とhead OIDの一致で行い、修正前の必須check成功はその根拠から外す。

## PRのマージ

レビューコメントの確認と必須checkが完了した後にPR番号から`headRefOid`と`mergeStateStatus`を再取得し、確認対象のcommitと一致し、`mergeStateStatus=CLEAN`であることを確認する。
一致しない場合は外部状態と再開点を報告して停止する。
マージ操作では操作直前に取得したhead OIDを原子的な一致条件に使い、明示的なマージコミットを作成する。自動マージとbranch削除は指定しない。`gh`がこの条件を受理する形式は実行直前のヘルプで確定する。条件を保証できない場合はマージを実行しない。

マージコマンドは1回だけ実行する。失敗した場合は再試行せず、出力された失敗理由と再開点を報告する。

## マージ後のbranch同期とCI

マージ後に`origin/master`をfetchし、PR番号から操作直前に取得した`mergeCommit.oid`が同じcommitを指すことを確認する。
`mergeCommit.oid`はこの一致確認だけに使い、以降のGit操作は`origin/master`を基準にする。

```sh
git fetch origin master
git rev-parse --short=7 origin/master
```

`origin/develop`の更新はローカルbranchを操作元にせず、`origin/master`と宛先refを明示したrefspecでpushする。

```sh
git push origin origin/master:refs/heads/develop
git fetch origin develop master
git rev-parse --short=7 origin/develop
git rev-parse --short=7 origin/master
```

`origin/develop`と`origin/master`の7文字以上の一意な短縮OIDが一致することを確認する。
マージ前の判定でローカル`develop`の同期を試みるとした場合は、同期を実行する直前に、基準refを`origin/master`とした「ローカルdevelop同期条件」を再取得して判定する。
`develop` worktreeがマージ前に保持した絶対パスと一致することも確認する。
すべて満たす場合だけ、続けて次を実行する。

```sh
git -C <develop worktreeの絶対パス> merge --ff-only origin/master
git -C <develop worktreeの絶対パス> rev-parse --short=7 develop
```

実行後に対象worktreeで取得した`develop`が`origin/master`の短縮OIDと一致することを確認する。
再取得した条件のいずれかが成立しない場合は、ローカル`develop`の同期だけを省略する。対象worktreeと既存の未コミット差分に加え、ローカルbranchも変更せずリモートの完遂を維持する。完了報告には、省略した条件と対象worktreeの絶対パスを記録する。ローカル`develop`の短縮OIDと`origin/master`の短縮OIDも記録する。

マージ後のmaster pushと同期後のdevelop pushに対するCIは、同じツリーでの再実行であるため待機を省く（努力目標。待つと完了が遅れるだけである）。
`master`へは`develop`からのリリースPRだけをマージし、マージ後は`develop`を`master`へ同期するため、マージコミットのツリーはマージ前の確認で必須checkの成功を確認したPR headのツリーと同じになる。同期で`develop`へ載るのも同じマージコミットである。
両pushのCIは同じ中身の再実行になり、待機しても完了までの時間が延びるだけである。
`Release statusLine`はmaster pushのCI成功を契機に起動するため、statuslineの変更を含む場合のmaster CIの結論は「条件付きRelease検収」が待つRelease runの成否で確かめる。

## 条件付きRelease検収

マージコミットの第一親との差分を読み取り、`rust/claude-statusline/`の変更有無を判定する。
変更がない場合はRelease検収を省略する。

変更がある場合は、`origin/master`の完全OIDに対応する`Release statusLine` runを候補とし、各候補を完全なdatabase IDで特定してジョブの状態を調べる。`prepare`が省略されずに実行されたrunを検収対象として終端まで待つ。`gate`以外のジョブが全て省略されたrunは対象から外し、次のrunを待つ。候補の`prepare`が未確定の間は、実行または省略が確定するまで観測する。
masterへのpushのCIを別に待機する工程は加えず、検収対象のRelease runの成否でmaster CIの結論を確かめる。master CIの失敗で対象のrunが作成されない場合は「失敗時の共通規定」に従う。
その後、manifestの版数に対応する`statusline-v<version>` tagが`origin/master`を指すことを確認する。
GitHub Releaseの存在と、次の既存asset名を確認する。

- `claude-statusline-x86_64-unknown-linux-gnu`
- `claude-statusline-x86_64-pc-windows-msvc.exe`

runの特定と終端観測、Releaseのasset確認、tagの参照先確認には`gh`とGitの公開情報を使う。取得形式は各操作の直前にヘルプで確定し、同じ`origin/master`の完全OIDへ対応する結果だけを検収する。

Release runの失敗は「失敗時の共通規定」を適用する。tag、Releaseまたはassetの検収に失敗した場合は、外部状態、失敗工程、run URLおよび再開点を報告する。

## マージ後に到着したレビューの確認

GitHub Copilotのレビューは、対象PRのマージ後、CIの完了を待つ区間に到着する場合がある。
「マージ後のbranch同期とCI」と「条件付きRelease検収」を終えた時点で、`atk review-audit pending --repo <OWNER>/<REPO>`を実行する。終了コード0でJSONの`reviews`と`threads`を解釈でき、対象PRが両方に含まれない場合は、そのPRの取得と判定を省く。含まれる場合は、対象PRのCopilot由来のreview本文とreview threadを1回取得する。コマンドが非0で終わった場合とJSONを解釈できない場合は、`pending`の結果によらず対象PRのCopilot由来のreview本文とreview threadを1回取得して判定する。

取得、判定、GitHubへの記録および判定済みの記録は
`agent-toolkit/skills/process-wi/references/github-copilot-review-audit.md`が定める手順に従い、対象をそのPRへ限定して適用する。
本節が扱うのはこの1回の取得までとし、新しいレビューの生成要求や到着の能動的な待機は対象外とする。
その時点で未到着のレビューは、`agent-toolkit:process-wi`の選定工程が全Pull Requestを対象に実行する監査が次回以降に拾うため、本節の取得は1回とし、未到着分は次回の監査へ回す（努力目標。反復しても成果が増えない）。

要修正と分類した指摘は「レビューコメントの確認」の振り分けに従う。
同一セッションで是正する場合は、その是正を`develop`への通常の変更として扱い、本スキルのマージ工程を再実行せず、公開を次回のリリースPRへ委ねる。

## マージ後に`develop`へ加えた変更のCI確認

`agent-toolkit:process-wi`の終端から本スキルを実行した場合に限り、マージの完遂後に同じセッションで`develop`へ加えた変更は、
pushが完了してCI runが起動した時点でその変更の公開工程を終え、CIの完了を待たない。
その変更は次回のリリースPRの「マージ前の確認」が必須checkの完了を待つ対象へ入り、
`master`は必須CIを通過したマージコミットだけで更新されるため、その時点でCIの結論を確定しなくても未検証の変更は`develop`に留まる。
省略するのはCIの完了待ちだけとし、変更に対応する変更範囲の検証は通常どおり成功させてからpushする。
省略したCIのrun URLは完了報告へ残す。

本節の適用範囲はマージの完遂後に`develop`へ加えた変更とし、本スキルのマージ工程は対象外とする。
「マージ前の確認」と「マージ後のbranch同期とCI」が定めるCIの検収は、それぞれの節の条件のまま維持する。

## 完了条件と失敗時の扱い

成功時に次を取得する。

```sh
git rev-parse --short=7 origin/develop
git rev-parse --short=7 origin/master
git status --short
```

`origin/develop`と`origin/master`の短縮OIDが一致し、必須CIと必要なRelease検収が成功した場合だけ完了とする。
あわせて「マージ後に到着したレビューの確認」を1回実施し、取得した指摘の分類と処置を確定していることを完了条件とする。
マージの完遂後に`develop`へ加えた変更のCI完了待ちは、「マージ後に`develop`へ加えた変更のCI確認」の条件が成立する場合に完了条件から外す。
ローカル`develop`を同期した場合は、`git rev-parse --short=7 develop`も`origin/master`の短縮OIDと一致することを確認する。
完了条件はリモートの状態で判定し、ローカルの作業ツリーとローカルbranchの状態はその判定から外す。同期を実施した場合はローカル`develop`の参照を更新し、同期を省略した場合は本手順がローカルへ書き込まないため、待機中にユーザーが加えた変更もそのまま残る。
`git status --short`の出力は合否判定に使わず、完了報告へ添える現状の情報として扱う。
完了報告では、リモートの完了と、ローカル`develop`を同期したかどうかを区別して示す。
