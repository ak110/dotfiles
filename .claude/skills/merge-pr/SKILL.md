---
name: merge-pr
description: 「PRをマージして」などの明示依頼を受領したとき、対象PRを検査し、マージ後のbranch同期、CI及び必要なRelease検収を完遂する
---

# PRマージ完遂

このスキルは、明示的なPRマージ依頼を受領した場合と、`agent-toolkit:process-wi`の終端で日次リリースの条件が成立した場合だけ実行する。
条件は`dotfiles-release`スキルの「developとmasterのリリース運用」が定める。
PRが存在するという観測だけでは起動しない。

## 失敗時の共通規定

いずれの工程が失敗した場合も、自動再試行、auto-merge、自動修復、自動rollbackのいずれも行わない。
成立済みの外部状態を保持し、失敗した工程、外部状態、run URL及び再開点を報告して停止する。
無人の再試行と巻き戻しは、GitHubの公開状態を利用者が観測しないまま変えるためである。

## 対象の選択

PR番号又はPR URLが指定された場合は、その対象を読み取る。
引数がない場合は、`develop`から`master`へのopen PRを一覧し、1件だけなら対象にする。
0件または複数件の場合は、PR番号又はURLの指定を求めて状態を変更せず停止する。
明示対象のheadが`develop`以外、baseが`master`以外の場合は、状態を変更せず停止する。

対象の確認には次の読み取りコマンドを使う。

```sh
gh pr view <PR番号またはURL> --repo ak110/dotfiles --json number,url,state,isDraft,mergeable,headRefName,headRefOid,baseRefName,baseRefOid,mergeCommit
gh pr list --repo ak110/dotfiles --state open --base master --head develop --json number,url,state,isDraft,mergeable,headRefName,headRefOid,baseRefName,baseRefOid
```

引数省略時の一覧は`develop`から`master`への候補だけを対象にする。
GitHubの設定でhead branchを`develop`だけに制限する操作は行わない。

## マージ前の検査

`git fetch origin develop master`でremote-tracking refを更新し、`origin/develop`とPR番号から操作直前に取得した`headRefOid`が同じcommitを指すことを確認する。
PRはopenかつdraftでなく、baseが`master`、headが`develop`で、mergeableが成立していなければならない。
マージの前提はこれらのリモート側の条件だけとし、作業ツリーのclean、現在branch及びローカル`develop`の位置を前提にしない。
ローカルの状態を理由にマージを停止しない。

ローカル`develop`を同期するかどうかは、マージの前提とは分けて次の読み取りコマンドで判定し、判定結果をマージ後まで保持する。

```sh
git status --porcelain
git rev-parse --abbrev-ref HEAD
git rev-parse --short=7 origin/develop
git merge-base --is-ancestor HEAD origin/develop
```

`git status --porcelain`の出力が空で、現在branchが`develop`で、`git merge-base --is-ancestor HEAD origin/develop`が終了コード0を返す場合だけ、マージ後にローカル`develop`の同期を試みる。
いずれかを満たさない場合は、既存の未コミット差分とローカルbranchを変更せず保持し、リモートだけでリリースを完遂する。
この判定はマージ前時点の見込みであり、同期を実行してよいかはマージ後に同じ観点を再取得して確定する。

必須checkの完了を次のコマンドで待つ。

```sh
gh pr checks <PR番号またはURL> --repo ak110/dotfiles --required --watch --fail-fast
```

必須checkの失敗、mergeableでない状態、PR head OIDの変化又は検査対象の曖昧さがある場合は、外部状態と再開点を報告して停止する。

## レビューコメントの確認

必須checkの待機と並行して、対象PRのレビューコメントを取得する。

```sh
gh api repos/ak110/dotfiles/pulls/<PR番号>/reviews
gh api repos/ak110/dotfiles/pulls/<PR番号>/comments
```

各指摘は対象の実装と規範を読んで妥当性を判定する。
成立する指摘は`agent-toolkit/rules/01-agent.md`「完遂と先送り」の判定を適用し、
同一セッションで対応する指摘と次セッション以降へ回す指摘へ分ける。
次セッション以降へ回す指摘だけをAWIへ登録する。
成立しない指摘は登録せず、判定の根拠を報告へ残す。
全指摘の判定、必要な同一セッションの是正及びAWI登録を完了してからマージへ進む。
同一セッションで是正した場合は、PR番号から修正後のPR headを再取得し、新たな検査対象として「マージ前の検査」を再実行する。
修正前の必須check成功を流用せず、修正後のPR headに対する必須check成功とhead OIDの一致を再検収する。

## PRのマージ

レビューコメントの確認と必須checkが完了した後にPR番号から`headRefOid`を再取得し、検査対象のcommitと一致することを確認する。
一致しない場合は外部状態と再開点を報告して停止する。
一致した`headRefOid`を`--match-head-commit`へ渡して明示的なマージコミットを作成する。
`--auto`及び`--delete-branch`は指定しない。

```sh
gh pr merge <PR番号またはURL> --repo ak110/dotfiles --merge --match-head-commit <PR番号から操作直前に取得したheadRefOid>
```

マージコマンドが失敗した場合は、出力された失敗理由と再開点を報告する。

## マージ後のbranch同期とCI

マージ後に`origin/master`をfetchし、PR番号から操作直前に取得した`mergeCommit.oid`が同じcommitを指すことを確認する。
`mergeCommit.oid`はこの照合だけに使い、以降のGit操作は`origin/master`を正本とする。

```sh
git fetch origin master
git rev-parse --short=7 origin/master
```

push前に管理対象一時領域を作成し、`origin/develop`のCI runをbaselineへ保存する。
baseline作成と同期pushの順序を変更しない。push後に同期先のrefを再取得して、develop CIの待機を省略できるか判定する。
`--repo`、`--forge`、`--ref`及び`--source-ref`は毎回明示する。
`origin/develop`の更新はローカルbranchを操作元にせず、`origin/master`と宛先refを明示したrefspecでpushする。

```sh
uv run --project agent-toolkit --locked --no-default-groups agent-toolkit/agent_toolkit/wait_ci.py --write-baseline <baselineの絶対パス> --repo ak110/dotfiles --forge github --ref refs/heads/develop --source-ref origin/develop --sha origin/master
git push origin origin/master:refs/heads/develop
git fetch origin develop master
git rev-parse --short=7 origin/develop origin/master
```

`origin/develop`と`origin/master`の7文字以上の一意な短縮OIDが一致することを確認する。
マージ前の判定でローカル`develop`の同期を試みるとした場合は、同期を実行する直前に次を再取得する。

```sh
git status --porcelain
git rev-parse --abbrev-ref HEAD
git merge-base --is-ancestor HEAD origin/master
```

`git status --porcelain`の出力が空で、現在branchが`develop`で、`git merge-base --is-ancestor HEAD origin/master`が終了コード0を返すことをすべて満たす場合だけ、続けて次を実行する。
`git merge --ff-only origin/master`は対象branchを引数に取らず現在branchを更新するため、この再取得を省いて実行しない。

```sh
git merge --ff-only origin/master
git rev-parse --short=7 develop
```

実行後に`git rev-parse --short=7 develop`が`origin/master`の短縮OIDと一致することを確認する。
再取得した観点のいずれかが成立しない場合は、ローカル`develop`の同期だけを省略し、既存の未コミット差分とローカルbranchを変更せずリモートの完遂を維持する。

develop CIの待機は、masterで検収したマージコミットとdevelopへ同期したコミットが同一であり、現行CI定義にdevelop固有job、branchで分岐する追加検査、外部検査がないことを確認できる場合だけ省略する。commit不一致、CI構成の判定不能、固有検査の存在又はrun識別の曖昧さがある場合は、develop push前のbaselineを用いる既存の待機経路へ戻す。必要なRelease statuslineのrun・タグ・GitHub Release・2成果物、`origin/develop`と`origin/master`が同じcommitを指す最終照合は省略しない。master CIの待機を省略できる条件は本節の後段が定める。

```sh
# OID一致かつdevelop固有検査なしの条件が成立しない場合だけ実行する。
uv run --project agent-toolkit --locked --no-default-groups agent-toolkit/agent_toolkit/wait_ci.py --baseline <baselineの絶対パス> --repo ak110/dotfiles --forge github --ref refs/heads/develop --source-ref origin/develop --sha origin/master
```

現行の`.github/workflows/ci.yaml`は全branchのpushに共通jobを実行し、develop固有jobを持たない。`audit.yaml`はschedule／manual、`release-statusline.yaml`はmaster CI後のRelease検収であるため、develop固有検査として扱わない。CI定義が変化した場合は省略条件を再判定する。

master CIの待機は、次の4つをすべて確認できる場合だけ省略する。いずれか1つでも確認できない場合は省略せず、後段の待機経路をそのまま実行する。

- マージコミットのツリーがPR headのツリーと同一である。
  PR番号から操作直前に`headRefOid`を取得する。
  `git rev-parse --short=7 origin/master^{tree} <headRefOid>^{tree}`が返す2行が同じ値であり、`git diff --name-only <headRefOid> origin/master`の出力が0行であることで判定する
- PR番号から特定したhead commitを対象とし、`push` eventかつ`develop` head branchであるCI runが`success`で完了している
- 「条件付きRelease検収」の判定で、マージコミットの第一親との差分に`rust/claude-statusline/`が含まれず、Release検収が不要である
- 現行CI定義にmaster固有のjob、master向けにだけ実行される追加検査及び外部検査がない

`release-statusline.yaml`はCIの成功を契機に起動し、そのgateは`push` event・`success`・`master` head branchの3条件で対象を絞る。`rust/claude-statusline/`に差分がある場合はmaster CIの成功が後続工程の前提になるため、当該差分がある場合は省略しない。
現行の`.github/workflows/ci.yaml`は全branchのpushへ共通jobを実行し、master固有jobを持たない。branchで分岐する条件は`develop`から`master`へのpull_requestイベントで一部stepを省く分岐だけであり、`push` eventのjob構成はbranchによらず同一である。CI定義が変化した場合は省略条件を再判定する。

master pushのCIは、`origin/master`、`push` event及び`master` head branchに一致するrunを一覧から特定する。
`gh run list --commit`へ渡す値は、外部インターフェースが要求するため`origin/master`から操作直前に完全OIDへ解決し、当該呼び出しだけに用いる。
runの完全なdatabase IDを取得した後、公式CLIで待機する。

```sh
gh run list --repo ak110/dotfiles --workflow CI --commit <origin/masterから操作直前に解決した完全OID> --json databaseId,event,headBranch,headSha,status,conclusion,url
gh run watch <run ID> --repo ak110/dotfiles --compact --exit-status
```

run登録前は読み取り専用の一覧取得を継続する。
自作のshell sleep loopでCI待機を実装しない。

## 条件付きRelease検収

マージコミットの第一親との差分を読み取り、`rust/claude-statusline/`の変更有無を判定する。
変更がない場合はRelease検収を省略する。

変更がある場合は、同じ`origin/master`を対象とする`Release statusLine` runを完全なdatabase IDで特定して待機する。
その後、manifestの版数に対応する`statusline-v<version>` tagが`origin/master`を指すことを確認する。
GitHub Releaseの存在と、次の既存asset名を確認する。

- `claude-statusline-x86_64-unknown-linux-gnu`
- `claude-statusline-x86_64-pc-windows-msvc.exe`

```sh
gh run list --repo ak110/dotfiles --workflow 'Release statusLine' --commit <origin/masterから操作直前に解決した完全OID> --json databaseId,event,headBranch,headSha,status,conclusion,url
gh run watch <Release run ID> --repo ak110/dotfiles --compact --exit-status
gh release view statusline-v<version> --repo ak110/dotfiles --json assets,tagName,targetCommitish
gh api repos/ak110/dotfiles/git/ref/tags/statusline-v<version> --jq .object.sha
```

Release run、tag、Release又はassetの検収に失敗した場合は、外部状態、失敗工程、run URL及び再開点を報告する。

## マージ後に到着したレビューの確認

GitHub Copilotのレビューは、対象PRのマージ後、CIの完了を待つ区間に到着する場合がある。
「マージ後のbranch同期とCI」と「条件付きRelease検収」を終えた時点で、対象PRのCopilot由来のreview本文とreview threadを1回取得する。

取得、判定、GitHubへの記録及び判定済みの記録は
`agent-toolkit/skills/process-wi/references/github-copilot-review-audit.md`を正本とし、対象を当該PRへ限定して適用する。
本節では新しいレビューの生成を要求せず、到着を能動的に待機しない。
当該時点で未到着のレビューは、`agent-toolkit:process-wi`の選定工程が全Pull Requestを対象に実行する監査が次回以降に拾うため、
本節で取得を繰り返さない。

要修正と分類した指摘は`agent-toolkit/rules/01-agent.md`「完遂と先送り」の判定を適用し、
同一セッションで是正する指摘と次セッション以降へ回す指摘へ分ける。
次セッション以降へ回す指摘だけをAWIへ登録する。
同一セッションで是正する場合は、当該是正を`develop`への通常の変更として扱い、本スキルのマージ工程を再実行しない。
成立しない指摘は登録せず、判定の根拠を報告へ残す。

## マージ後に`develop`へ加えた変更のCI確認

`agent-toolkit:process-wi`の終端から本スキルを実行した場合に限り、マージの完遂後に同じセッションで`develop`へ加えた変更は、
pushの完了とCI runの起動をもって当該変更の公開工程を終え、CIの完了を待たない。
当該変更は次回のリリースPRの「マージ前の検査」が必須checkの完了を待つ対象へ入り、
`master`は必須CIを通過したマージコミットだけで更新されるため、当該時点でCIの結論を確定しなくても未検証の変更が`master`へ入らない。
省略するのはCIの完了待ちだけとし、変更に対応する近接検査は通常どおり成功させてからpushする。
省略したCIのrun URLは完了報告へ残す。

本節は本スキルのマージ工程自体へ適用しない。
「マージ前の検査」と「マージ後のbranch同期とCI」が定めるCIの検収は、当該節の条件のまま維持する。

## 完了条件と失敗時の扱い

成功時に次を取得する。

```sh
git rev-parse --short=7 origin/develop origin/master
git status --short
```

`origin/develop`と`origin/master`の短縮OIDが一致し、必須CIと必要なRelease検収が成功した場合だけ完了とする。
あわせて「マージ後に到着したレビューの確認」を1回実施し、取得した指摘の分類と処置を確定していることを完了条件とする。
マージの完遂後に`develop`へ加えた変更のCI完了待ちは、「マージ後に`develop`へ加えた変更のCI確認」の条件が成立する場合に完了条件から外す。
ローカル`develop`を同期した場合は、`git rev-parse --short=7 develop`も`origin/master`の短縮OIDと一致することを確認する。
ローカルの作業ツリーとローカルbranchの状態は完了条件にしない。同期を実施した経路ではローカル`develop`の参照を更新し、同期を省略した経路では本手順がローカルへ書き込まないため、待機中に利用者が加えた変更もそのまま残る。
`git status --short`の出力は合否判定に使わず、完了報告へ添える現状の情報として扱う。
完了報告では、リモートの完了と、ローカル`develop`を同期したかどうかを区別して示す。

マージ後のCI又はReleaseが失敗した場合は、待機終了後の診断で次の読み取りコマンドを使って詳細ログを取得する。

```sh
gh run view <失敗したrun ID> --repo ak110/dotfiles --log-failed
```

詳細ログを取得できない場合も、元のCI又はReleaseの失敗を失敗工程として保持し、ログ取得の失敗を併記する。

マージ後のCI又はReleaseが失敗した場合も、成立済みの外部状態、失敗した工程、run URL及び再開点を報告して停止する。
