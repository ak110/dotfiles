---
name: merge-pr
description: 「PRをマージして」などの明示依頼を受領したとき、対象PRを検査し、マージ後のbranch同期、CI及び必要なRelease検収を完遂する
---

# PRマージ完遂

このスキルは、明示的なPRマージ依頼を受領した場合と、`agent-toolkit:process-wi`の終端で日次リリースの条件が成立した場合だけ実行する。
条件は`dotfiles-release`スキルの「developとmasterのリリース運用」が定める。
PRが存在するという観測を起動の契機から外し、前記2つの場合だけ起動する。

## 失敗時の共通規定

CIとRelease以外の工程が失敗した場合は、成立済みの外部状態を保持し、失敗した工程、外部状態、run URL及び再開点を報告して停止する。PR作成・マージ操作の再試行とauto-merge、自動修復、自動rollbackは行わない。

CIとReleaseのrunが失敗した場合は、run全体の終端と失敗ログを確認し、`agent-toolkit:bugfix`の`references/ci-failure-handling.md`「再現性」に従って原因を分類する。ネットワーク断、配布元のタイムアウト、レート制限、ランナー障害など、ログで外部一時要因を疑える場合は、同書の広域障害確認を済ませてから、同じ原因につき失敗jobを一度だけ再実行する。

runの失敗ログ取得、失敗jobの再実行、run全体の終端観測には`gh`を使い、各操作の受理形式は実行直前のヘルプで確定する。

再実行後のログと結果で検収を続ける。再失敗、又は初回ログで外部一時要因を疑えない場合は、成立済みの外部状態、失敗工程、run URL及び再開点を報告して停止する。ログを取得できない場合も、元のrunの失敗を保持して停止する。

## 対象の選択

PR番号又はPR URLが指定された場合は、その対象を読み取る。
引数がない場合は、`develop`から`master`へのopen PRを一覧し、1件だけなら対象にする。
0件または複数件の場合は、PR番号又はURLの指定を求めて状態を変更せず停止する。
明示対象のheadが`develop`以外、baseが`master`以外の場合は、状態を変更せず停止する。

対象の確認には`gh`でPRの番号、URL、状態、draft、merge可否、headとbaseのbranch・OID、merge commitを取得する。受理形式と取得可能な項目は実行直前のヘルプで確定する。

GitHubの設定でhead branchを`develop`だけに制限する操作は行わず、現在の設定のまま維持する。

## ローカルdevelop同期条件

利用者の未コミット変更とローカルbranchを壊さない場合だけ、ローカル`develop`を同期する。
次の全てが成立することを「ローカルdevelop同期条件」と呼ぶ。基準refは参照する節が指定する。

- `git worktree list --porcelain`の出力で`branch refs/heads/develop`を持つblockが1件だけある。その`worktree`行の絶対パスを`develop` worktreeとする
- `develop` worktreeの`git status --porcelain`の出力が空である
- 現在branchが`develop`である
- rebase・merge・cherry-pickの中断状態が無い。中断状態は対象worktreeに対応するGit管理領域の`rebase-merge`、`rebase-apply`、`MERGE_HEAD`と`CHERRY_PICK_HEAD`の実在で判定する
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

`git fetch origin develop master`でremote-tracking refを更新し、`origin/develop`とPR番号から操作直前に取得した`headRefOid`が同じcommitを指すことを確認する。
PRはopenかつdraftでなく、baseが`master`、headが`develop`で、mergeableが成立していなければならない。
マージの前提と続行の判定はこれらのリモート側の条件だけで行い、作業ツリーのclean、現在branch及びローカル`develop`の位置はこの前提から外す。

ローカル`develop`を同期するかどうかは、マージの前提とは分けて、基準refを`origin/develop`とした「ローカルdevelop同期条件」で判定する。
成立する場合は`develop` worktreeの絶対パスを保持し、マージ後にそのworktreeでローカル`develop`の同期を試みる。
成立しない場合は、対象worktreeとローカル`develop`に加え、既存の未コミット差分も変更せず保持する。リモートだけでリリースを完遂する。
この判定はマージ前時点の見込みであり、同期を実行してよいかはマージ後に同じ条件を再取得して確定する。

必須checkは`gh`が返す当該PRの終了状態まで待つ。待機と必須checkの指定形式は実行直前のヘルプで確定する。

必須checkの失敗は該当runの終端とログを確認して「失敗時の共通規定」を適用する。mergeableでない状態、PR head OIDの変化又は確認対象の曖昧さがある場合は、外部状態と再開点を報告して停止する。

## レビューコメントの確認

必須checkの待機と並行して、対象PRのレビューコメントを取得する。

レビューと行コメントを、対象PR番号へ対応付けてGitHubから取得する。`gh`のAPI呼出形式は実行直前のヘルプで確定する。

各指摘は対象の実装と規範を読んで妥当性を判定する。
成立する指摘は`agent-toolkit/rules/01-agent.md`「完遂と先送り」の判定を適用し、
同一セッションで対応する指摘と次セッション以降へ回す指摘へ分ける。
次セッション以降へ回す指摘だけをAWIへ登録する。
成立しない指摘は登録せず、判定の根拠を報告へ残す。
全指摘の判定、必要な同一セッションの是正及びAWI登録を完了してからマージへ進む。
同一セッションで是正した場合は、PR番号から修正後のPR headを再取得し、新たな確認対象として「マージ前の確認」を再実行する。
検収は修正後のPR headに対する必須check成功とhead OIDの一致で行い、修正前の必須check成功はその根拠から外す。

## PRのマージ

レビューコメントの確認と必須checkが完了した後にPR番号から`headRefOid`を再取得し、確認対象のcommitと一致することを確認する。
一致しない場合は外部状態と再開点を報告して停止する。
マージ操作では操作直前に取得したhead OIDを原子的な一致条件に使い、明示的なマージコミットを作成する。自動マージとbranch削除は指定しない。`gh`がこの条件を受理する形式は実行直前のヘルプで確定する。条件を保証できない場合はマージを実行しない。

マージコマンドが失敗した場合は、出力された失敗理由と再開点を報告する。

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

マージ後のmaster pushと同期後のdevelop pushに対するCIは待機しない。
`master`へは`develop`からのリリースPRだけをマージし、マージ後は`develop`を`master`へ同期するため、マージコミットのツリーはマージ前の確認で必須checkの成功を確認したPR headのツリーと同じになる。同期で`develop`へ載るのも同じマージコミットである。
両pushのCIは同じ中身の再実行になり、待機しても完了までの時間が延びるだけである。
`Release statusLine`はmaster pushのCI成功を契機に起動するため、statuslineの変更を含む場合のmaster CIの結論は「条件付きRelease検収」が待つRelease runの成否で確かめる。

## 条件付きRelease検収

マージコミットの第一親との差分を読み取り、`rust/claude-statusline/`の変更有無を判定する。
変更がない場合はRelease検収を省略する。

変更がある場合は、同じ`origin/master`を対象とする`Release statusLine` runを完全なdatabase IDで特定して待機する。
その後、manifestの版数に対応する`statusline-v<version>` tagが`origin/master`を指すことを確認する。
GitHub Releaseの存在と、次の既存asset名を確認する。

- `claude-statusline-x86_64-unknown-linux-gnu`
- `claude-statusline-x86_64-pc-windows-msvc.exe`

runの特定と終端観測、Releaseのasset確認、tagの参照先確認には`gh`とGitの公開情報を使う。取得形式は各操作の直前にヘルプで確定し、同じ`origin/master`の完全OIDへ対応する結果だけを検収する。

Release runの失敗は「失敗時の共通規定」を適用する。tag、Release又はassetの検収に失敗した場合は、外部状態、失敗工程、run URL及び再開点を報告する。

## マージ後に到着したレビューの確認

GitHub Copilotのレビューは、対象PRのマージ後、CIの完了を待つ区間に到着する場合がある。
「マージ後のbranch同期とCI」と「条件付きRelease検収」を終えた時点で、対象PRのCopilot由来のreview本文とreview threadを1回取得する。

取得、判定、GitHubへの記録及び判定済みの記録は
`agent-toolkit/skills/process-wi/references/github-copilot-review-audit.md`が定める手順に従い、対象をそのPRへ限定して適用する。
本節が扱うのはこの1回の取得までとし、新しいレビューの生成の要求と到着の能動的な待機はその外に置く。
その時点で未到着のレビューは、`agent-toolkit:process-wi`の選定工程が全Pull Requestを対象に実行する監査が次回以降に拾うため、本節で取得を繰り返さない。

要修正と分類した指摘は「レビューコメントの確認」の振り分けに従う。
同一セッションで是正する場合は、その是正を`develop`への通常の変更として扱い、本スキルのマージ工程を再実行せず、公開を次回のリリースPRへ委ねる。

## マージ後に`develop`へ加えた変更のCI確認

`agent-toolkit:process-wi`の終端から本スキルを実行した場合に限り、マージの完遂後に同じセッションで`develop`へ加えた変更は、
pushの完了とCI runの起動をもってその変更の公開工程を終え、CIの完了を待たない。
その変更は次回のリリースPRの「マージ前の確認」が必須checkの完了を待つ対象へ入り、
`master`は必須CIを通過したマージコミットだけで更新されるため、その時点でCIの結論を確定しなくても未検証の変更は`develop`に留まる。
省略するのはCIの完了待ちだけとし、変更に対応する近接検証は通常どおり成功させてからpushする。
省略したCIのrun URLは完了報告へ残す。

本節の適用範囲はマージの完遂後に`develop`へ加えた変更とし、本スキルのマージ工程はその外に置く。
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
完了条件はリモートの状態で判定し、ローカルの作業ツリーとローカルbranchの状態はその判定から外す。同期を実施した場合はローカル`develop`の参照を更新し、同期を省略した場合は本手順がローカルへ書き込まないため、待機中に利用者が加えた変更もそのまま残る。
`git status --short`の出力は合否判定に使わず、完了報告へ添える現状の情報として扱う。
完了報告では、リモートの完了と、ローカル`develop`を同期したかどうかを区別して示す。
