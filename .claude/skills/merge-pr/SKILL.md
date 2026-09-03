---
name: merge-pr
description: 「PRをマージして」などの明示依頼を受領したとき、対象PRを検査し、マージ後のbranch同期、CI及び必要なRelease検収を完遂する
---

# PRマージ完遂

このスキルは、明示的なPRマージ依頼を受領した場合と、`agent-toolkit:process-feedbacks`の終端で日次リリースの条件が成立した場合だけ実行する。
条件は`docs/development/operations.md`「日次リリースの自動実施」が定める。
PRが存在するという観測だけでは起動しない。

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

`git fetch origin develop master`でremote-tracking refを更新し、`origin/develop`とPRの`headRefOid`が同じ完全OIDであることを確認する。
PRはopenかつdraftでなく、baseが`master`、headが`develop`で、mergeableが成立していなければならない。
マージの前提はこれらのリモート側の条件だけとし、作業ツリーのclean、現在branch及びローカル`develop`の位置を前提にしない。
ローカルの状態を理由にマージを停止しない。

ローカル`develop`を同期するかどうかは、マージの前提とは分けて次の読み取りコマンドで判定し、判定結果をマージ後まで保持する。

```sh
git status --porcelain
git rev-parse --abbrev-ref HEAD
git rev-parse origin/develop
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
自動再試行、auto-merge及びrollbackは行わない。

## レビューコメントの確認

必須checkの待機と並行して、対象PRのレビューコメントを取得する。

```sh
gh api repos/ak110/dotfiles/pulls/<PR番号>/reviews
gh api repos/ak110/dotfiles/pulls/<PR番号>/comments
```

各指摘は対象の実装と規範を読んで妥当性を判定する。
成立する指摘はフィードバックへ登録し、次セッション以降で正式に対応する。
マージはこの登録を待たずに進める。
成立しない指摘は登録せず、判定の根拠を報告へ残す。

## PRのマージ

必須check成功後にPR headの完全OIDを再取得し、取得値を`--match-head-commit`へ渡して明示的なマージコミットを作成する。
`--auto`及び`--delete-branch`は指定しない。

```sh
gh pr merge <PR番号またはURL> --repo ak110/dotfiles --merge --match-head-commit <PR headの完全OID>
```

マージコマンドが失敗した場合は自動で再実行せず、出力された失敗理由と再開点を報告する。

## マージ後のbranch同期とCI

マージ後にPRの`mergeCommit.oid`を取得し、完全OIDを`MERGE_OID`として保持する。
`origin/master`をfetchして`MERGE_OID`と一致することを確認する。

```sh
git fetch origin master
git rev-parse origin/master
```

push前に管理対象一時領域を作成し、`origin/develop`のCI runをbaselineへ保存する。
baseline作成と同期pushの順序を変更しない。push後に同期先のrefを再取得して、develop CIの待機を省略できるか判定する。
`--repo`、`--forge`、`--ref`及び`--source-ref`は毎回明示する。
`origin/develop`の更新はローカルbranchを操作元にせず、`MERGE_OID`と宛先refを明示したrefspecでpushする。

```sh
uv run --no-project --script agent-toolkit/scripts/wait_ci.py --write-baseline <baselineの絶対パス> --repo ak110/dotfiles --forge github --ref refs/heads/develop --source-ref develop --sha <MERGE_OID>
git push origin <MERGE_OID>:refs/heads/develop
git fetch origin develop master
git rev-parse origin/develop origin/master
```

`MERGE_OID`、`origin/develop`及び`origin/master`の完全OIDがすべて一致することを確認する。
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
git rev-parse develop
```

実行後に`git rev-parse develop`が`MERGE_OID`と一致することを確認する。
再取得した観点のいずれかが成立しない場合は、ローカル`develop`の同期だけを省略し、既存の未コミット差分とローカルbranchを変更せずリモートの完遂を維持する。

develop CIの待機は、masterで検収したマージコミットとdevelopへ同期したコミットの完全OIDが同一であり、現行CI定義にdevelop固有job、branchで分岐する追加検査、外部検査がないことを確認できる場合だけ省略する。OID不一致、CI構成の判定不能、固有検査の存在又はrun識別の曖昧さがある場合は、develop push前のbaselineを用いる既存の待機経路へ戻す。必要なRelease statuslineのrun・タグ・GitHub Release・2成果物、`origin/develop`と`origin/master`の最終完全OID照合は省略しない。master CIの待機を省略できる条件は本節の後段が定める。

```sh
# OID一致かつdevelop固有検査なしの条件が成立しない場合だけ実行する。
uv run --no-project --script agent-toolkit/scripts/wait_ci.py --baseline <baselineの絶対パス> --repo ak110/dotfiles --forge github --ref refs/heads/develop --source-ref develop --sha <MERGE_OID>
```

現行の`.github/workflows/ci.yaml`は全branchのpushに共通jobを実行し、develop固有jobを持たない。`audit.yaml`はschedule／manual、`release-statusline.yaml`はmaster CI後のRelease検収であるため、develop固有検査として扱わない。CI定義が変化した場合は省略条件を再判定する。

master CIの待機は、次の4つをすべて確認できる場合だけ省略する。いずれか1つでも確認できない場合は省略せず、後段の待機経路をそのまま実行する。

- マージコミットのツリーがPR headのツリーと同一である。`git rev-parse <MERGE_OID>^{tree} <PR headの完全OID>^{tree}`が返す2行が同じ値であり、`git diff --name-only <PR headの完全OID> <MERGE_OID>`の出力が0行であることで判定する
- PR headの完全OIDを対象とし、`push` eventかつ`develop` head branchであるCI runが`success`で完了している
- 「条件付きRelease検収」の判定で、マージコミットの第一親との差分に`rust/claude-statusline/`が含まれず、Release検収が不要である
- 現行CI定義にmaster固有のjob、master向けにだけ実行される追加検査及び外部検査がない

`release-statusline.yaml`はCIの成功を契機に起動し、そのgateは`push` event・`success`・`master` head branchの3条件で対象を絞る。`rust/claude-statusline/`に差分がある場合はmaster CIの成功が後続工程の前提になるため、当該差分がある場合は省略しない。
現行の`.github/workflows/ci.yaml`は全branchのpushへ共通jobを実行し、master固有jobを持たない。branchで分岐する条件は`develop`から`master`へのpull_requestイベントで一部stepを省く分岐だけであり、`push` eventのjob構成はbranchによらず同一である。CI定義が変化した場合は省略条件を再判定する。

master pushのCIは、完全OID、`push` event及び`master` head branchに一致するrunを一覧から特定する。
runの完全なdatabase IDを取得した後、公式CLIで待機する。

```sh
gh run list --repo ak110/dotfiles --workflow CI --commit <MERGE_OID> --json databaseId,event,headBranch,headSha,status,conclusion,url
gh run watch <run ID> --repo ak110/dotfiles --compact --exit-status
```

run登録前は読み取り専用の一覧取得を継続する。
自作のshell sleep loopでCI待機を実装しない。

## 条件付きRelease検収

マージコミットの第一親との差分を読み取り、`rust/claude-statusline/`の変更有無を判定する。
変更がない場合はRelease検収を省略する。

変更がある場合は、同じ`MERGE_OID`を対象とする`Release statusLine` runを完全なdatabase IDで特定して待機する。
その後、manifestの版数に対応する`statusline-v<version>` tagが`MERGE_OID`を指すことを確認する。
GitHub Releaseの存在と、次の既存asset名を確認する。

- `claude-statusline-x86_64-unknown-linux-gnu`
- `claude-statusline-x86_64-pc-windows-msvc.exe`

```sh
gh run list --repo ak110/dotfiles --workflow 'Release statusLine' --commit <MERGE_OID> --json databaseId,event,headBranch,headSha,status,conclusion,url
gh run watch <Release run ID> --repo ak110/dotfiles --compact --exit-status
gh release view statusline-v<version> --repo ak110/dotfiles --json assets,tagName,targetCommitish
gh api repos/ak110/dotfiles/git/ref/tags/statusline-v<version> --jq .object.sha
```

Release run、tag、Release又はassetの検収に失敗した場合は、自動修復や再試行をせず、外部状態、失敗工程、run URL及び再開点を報告する。

## 完了条件と失敗時の扱い

成功時に次を取得する。

```sh
git rev-parse origin/develop origin/master
git status --short
```

`origin/develop`と`origin/master`が`MERGE_OID`と一致し、必須CIと必要なRelease検収が成功した場合だけ完了とする。
ローカル`develop`を同期した場合は、`git rev-parse develop`も`MERGE_OID`と一致することを確認する。
ローカルの作業ツリーとローカルbranchの状態は完了条件にしない。同期を実施した経路ではローカル`develop`の参照を更新し、同期を省略した経路では本手順がローカルへ書き込まないため、待機中に利用者が加えた変更もそのまま残る。
`git status --short`の出力は合否判定に使わず、完了報告へ添える現状の情報として扱う。
完了報告では、リモートの完了と、ローカル`develop`を同期したかどうかを区別して示す。

マージ後のCI又はReleaseが失敗した場合は、待機終了後の診断で次の読み取りコマンドを使って詳細ログを取得する。

```sh
gh run view <失敗したrun ID> --repo ak110/dotfiles --log-failed
```

詳細ログを取得できない場合も、元のCI又はReleaseの失敗を失敗工程として保持し、ログ取得の失敗を併記する。

マージ後のCI又はReleaseが失敗しても、自動rollback、auto-merge及び自動再試行をせず、成立済みの外部状態を保持する。
成立済みの外部状態、失敗した工程、run URL及び再開点を報告して停止する。
