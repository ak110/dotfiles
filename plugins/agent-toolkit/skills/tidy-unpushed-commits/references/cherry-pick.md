# cherry-pick連鎖と全統合ファストパスの詳細

`tidy-unpushed-commits`スキルの実行フェーズで使う2つの経路と、conflict発生時の対応方針をまとめる。
退避refの作成（`refs/tidy-backup/${TS}`）はSKILL.md側で済ませている前提。

## 全統合ファストパス

### 適用条件（すべて満たす場合のみ）

- 整理対象範囲のコミットがひとつ残らず単一のsquashグループに入る
- 承認された新順序が`B1`のみ（他にコミットが残らない）

この条件下では後続コミットが存在しないため、`git reset --soft`を1回行うだけで畳める。

```bash
git reset --soft "$BASE"
git commit -F "$MSG_FILE"
```

残存コミットが1つでもある場合は適用不可。後述のcherry-pick連鎖にフォールバックする（末尾から順に`reset --soft`を繰り返すと後続コミットを巻き込むため）。

## cherry-pick連鎖（標準経路）

ファストパスに該当しない全ケースで使う。
detached HEAD上で承認済みの新順序を再構成し、最後にbranch refを付け替える。

```bash
git checkout --detach "$BASE"
```

承認済み新順序を先頭から1コミットずつcherry-pickする。

```bash
git cherry-pick <先頭コミットSHA>
```

squashグループを合流させる場合は、先頭を通常cherry-pickし、残りを`--no-commit`で重ね、最後に`--amend -F`で統合後メッセージを適用する。

```bash
git cherry-pick <先頭コミットSHA>
git cherry-pick --no-commit <2番目コミットSHA>
git commit --amend -F "$MSG_FILE"
```

すべてのcherry-pick完了後、元ブランチを新HEADに付け替える。

```bash
git branch -f "$BRANCH" HEAD
git checkout "$BRANCH"
```

## conflict発生時

- reorder起因: 該当箇所だけreorderを諦める。`git cherry-pick --abort`で巻き戻し、当該コミットを元の相対位置に戻して進行する。整理全体は中断しない
- squash起因: 自動解決を試みず即座に報告する。`git cherry-pick --abort`で中断し、退避refからロールバックして計画を再検討する
- `git checkout --theirs`/`--ours`による安易な解決は禁止。修正単位が壊れる
