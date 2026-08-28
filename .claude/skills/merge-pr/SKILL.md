---
name: merge-pr
description: 「PRをマージして」などの明示依頼を受領したとき、対象PRを検査し、マージ後のbranch同期、CI及び必要なRelease検収を完遂する
---

# PRマージ完遂

このスキルは、明示的なPRマージ依頼を受領した場合だけ実行する。
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

作業ツリーがcleanであることを確認する。
現在branchは`develop`とし、ローカル`develop`、`origin/develop`及びPRの`headRefOid`を完全OIDで比較する。
ローカルだけが遅れている場合はfast-forwardで同期できるが、ローカル固有commit又は差分がある場合は停止する。
PRはopenかつdraftでなく、baseが`master`、headが`develop`で、mergeableが成立していなければならない。

必須checkの完了を次のコマンドで待つ。

```sh
gh pr checks <PR番号またはURL> --repo ak110/dotfiles --required --watch --fail-fast
```

必須checkの失敗、mergeableでない状態、PR head OIDの変化又は検査対象の曖昧さがある場合は、外部状態と再開点を報告して停止する。
自動再試行、auto-merge及びrollbackは行わない。

## PRのマージ

必須check成功後にPR headの完全OIDを再取得し、取得値を`--match-head-commit`へ渡して明示的なマージコミットを作成する。
`--auto`及び`--delete-branch`は指定しない。

```sh
gh pr merge <PR番号またはURL> --repo ak110/dotfiles --merge --match-head-commit <PR headの完全OID>
```

マージコマンドが失敗した場合は自動で再実行せず、出力された失敗理由と再開点を報告する。

## マージ後のbranch同期とCI

マージ後にPRの`mergeCommit.oid`を取得し、完全OIDを`MERGE_OID`として保持する。
`origin/master`をfetchして`MERGE_OID`と一致することを確認し、ローカル`develop`をfast-forwardする。

```sh
git fetch origin master
git rev-parse origin/master
git merge --ff-only origin/master
```

push前に管理対象一時領域を作成し、`origin/develop`のCI runをbaselineへ保存する。
baseline作成と同期pushの順序を変更しない。push後に同期先のrefを再取得して、develop CIの待機を省略できるか判定する。
`--repo`、`--forge`、`--ref`及び`--source-ref`は毎回明示する。

```sh
uv run --no-project --script /home/aki/dotfiles/agent-toolkit/scripts/wait_ci.py --write-baseline <baselineの絶対パス> --repo ak110/dotfiles --forge github --ref refs/heads/develop --source-ref develop --sha <MERGE_OID>
git push origin develop
git fetch origin develop master
git rev-parse develop origin/develop origin/master
```

`MERGE_OID`とローカル`develop`、`origin/develop`及び`origin/master`の完全OIDがすべて一致することを確認する。develop CIの待機は、masterで検収したマージコミットとdevelopへ同期したコミットの完全OIDが同一であり、現行CI定義にdevelop固有job、branchで分岐する追加検査、外部検査がないことを確認できる場合だけ省略する。OID不一致、CI構成の判定不能、固有検査の存在又はrun識別の曖昧さがある場合は、develop push前のbaselineを用いる既存の待機経路へ戻す。master CI、必要なRelease statuslineのrun・タグ・GitHub Release・2成果物、local develop・origin/develop・origin/masterの最終完全OID照合は省略しない。

```sh
# OID一致かつdevelop固有検査なしの条件が成立しない場合だけ実行する。
uv run --no-project --script /home/aki/dotfiles/agent-toolkit/scripts/wait_ci.py --baseline <baselineの絶対パス> --repo ak110/dotfiles --forge github --ref refs/heads/develop --source-ref develop --sha <MERGE_OID>
```

現行の`.github/workflows/ci.yaml`は全branchのpushに共通jobを実行し、develop固有jobを持たない。`audit.yaml`はschedule／manual、`release-statusline.yaml`はmaster CI後のRelease検収であるため、develop固有検査として扱わない。CI定義が変化した場合は省略条件を再判定する。

master pushのCIは、完全OID、`push` event及び`master` head branchに一致するrunを一覧から特定する。
runの完全なdatabase IDを取得した後、公式CLIで待機する。

```sh
gh run list --repo ak110/dotfiles --workflow CI --commit <MERGE_OID> --json databaseId,event,headBranch,headSha,status,conclusion,url
gh run watch <run ID> --repo ak110/dotfiles --exit-status
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
gh run watch <Release run ID> --repo ak110/dotfiles --exit-status
gh release view statusline-v<version> --repo ak110/dotfiles --json assets,tagName,targetCommitish
gh api repos/ak110/dotfiles/git/ref/tags/statusline-v<version> --jq .object.sha
```

Release run、tag、Release又はassetの検収に失敗した場合は、自動修復や再試行をせず、外部状態、失敗工程、run URL及び再開点を報告する。

## 完了条件と失敗時の扱い

成功時に次の値を完全OIDで再取得する。

```sh
git rev-parse develop origin/develop origin/master
git status --short
```

ローカル`develop`、`origin/develop`及び`origin/master`が`MERGE_OID`と一致し、作業ツリーがcleanである場合だけ完了とする。

マージ後のCI又はReleaseが失敗しても、自動rollback、auto-merge及び自動再試行をせず、成立済みの外部状態を保持する。
成立済みの外部状態、失敗した工程、run URL及び再開点を報告して停止する。
