---
name: tidy-unpushed-commits
description: 未プッシュコミットを安全に整理するスキル。ユーザーが「コミットをまとめたい」「履歴を整頓したい」「squashしたい」「reorderしたい」「未プッシュコミットを整理したい」などと明示的に言及したときのみ使う。積極的な自動トリガーは避け、ユーザーの意思が明確な場面に限定する。軽微なドキュメント修正やCI・ツールチェイン周りの修正コミットをまとめたり、コミット順を調整したりする。雑なメッセージで積まれたコミットや、まだコミットしていない軽微な変更も同じサイクルで取り込める。退避refとコンテンツハッシュ検証によって修正単位が壊れないことを機械的に担保する。
user-invocable: true
---

# 未プッシュコミットの整理

未プッシュコミットのsquash/reorderを、退避refとコンテンツハッシュ検証で修正単位を壊さずに行う。
`git reset --hard`の後に一括ステージしてコミットしなおすような乱暴な手順は取らない。

## 適用条件と非適用条件

本スキルはユーザーが明示的に依頼したときのみ使う。軽微なコミットが溜まっているだけで自動起動してはならない。
`/tidy-unpushed-commits`呼び出し、または「コミットをまとめたい」などの明確な指示を入口にする。

以下のいずれかに該当する場合は使わず、手動対応を促す。

- 整理対象範囲にmerge commitが1つでも含まれる
- Conventional Commitsの慣用的解釈で接頭辞を運用していない
- 整理対象が20コミットを超える
- 整理対象範囲にpush済みコミットが含まれる
- upstream未設定で`@{u}`が解決できない

## 前提確認

以下を順に実行し、整理対象範囲と前提条件を確定する。

```bash
git status
git -c fetch.prune=false -c fetch.pruneTags=false fetch --quiet
git log --oneline --decorate @{u}..HEAD
BASE="$(git merge-base HEAD @{u})"
git rev-list --merges "$BASE..HEAD"
git config --get rerere.enabled
```

- `fetch.prune`/`fetch.pruneTags`はローカル限定の退避refを巻き込みで消しうるため、このfetchに限り明示的に無効化する
- 各コミットに`origin/<branch名>`が付いていればpush済みである。範囲から除外する
- `BASE`より先（古い側）は絶対に触らない
- `git rev-list --merges`の出力が空でなければ即中断して報告する
- `rerere.enabled`が`true`の場合はユーザーに警告してから進める（意図せぬ自動解決を避けるため）

## uncommitted changesの取り込み

worktreeまたはindexに変更がある場合、`git diff`などで内容を提示して判断を仰ぐ。自動分類してはならない。
判断の候補ごとの分岐は次のとおり。

- 無害として取り込む: `git add -A && git commit -m "wip: tidy-unpushed-commits intake"`でWIPコミット化し、整理サイクルに組み込む。WIPコミットは退避refより前に作る（退避refは真の起点を指す必要があるため）
- 機能変更が混じっている: スキルを中断する。先に意味ある単位でコミットしてから再呼び出しするよう伝える
- 整理サイクルから除外する: `git stash --include-untracked`で退避し、整理完了後に`git stash pop`で戻す

## コミットの分類

整理対象範囲の各コミットを以下3カテゴリに分類する。判断基準はシンプルに保ち、迷ったらユーザーに聞く。

| カテゴリ | 接頭辞 | 整理上の扱い |
| --- | --- | --- |
| 無害 | `docs:`/`chore:`/`ci:`/`build:`/`style:`（スコープ付きも同様） | squash対象。機能コミットを跨いで自由にreorderしてよい |
| 機能 | `feat:`/`fix:`/`perf:` | 相対順序を変えない。原則squashしない（後述のペア例外のみ可） |
| 怪しい | 上記以外 | 自動分類しない。`git show`で内容を提示してユーザーに個別確認する |

怪しいコミットの代表例は、接頭辞なし/`refactor:`/`test:`/雑なWIPメッセージなど。
推奨動作の候補としては「無害側に取り込む」「隣接機能コミットにペアsquash」「機能コミットとして位置固定」「整理サイクルから除外」のいずれかを内容に応じて提案し、最終判断はユーザーが行う。

ペア例外: 機能コミットの直前または直後に同一論理単位と見受けられる`refactor:`/`test:`/`fix:`などがある場合、その機能コミットにまとめる候補を提案できる。判定が曖昧なので自動squashせず、整理計画で必ず確認を取る。

squashグループの機械的提案は以下に限る。機能コミットどうしのsquashは提案しない。

1. 同じ接頭辞種別または同じ主要ディレクトリを対象とする無害コミット同士
2. 上記ペア例外による機能コミットへの吸収

提案したグループは整理計画の確認事項で明示承認を得る（無害かつ自明な場合を除く）。承認なしに統合してはならない。

## 統合後メッセージの方針

squashグループのメッセージは原則として統合後の差分（`git diff <group-parent>..<group-last>`）と元メッセージ群を踏まえてConventional Commits形式で新しく起こす。
ペア例外で機能コミット側に寄せる等、片側のメッセージをそのまま採用する場合は整理計画に明記する。
メッセージは`/tmp/tidy-msg-<groupId>.txt`などのファイルに保存し、`git commit -F <file>`/`git commit --amend -F <file>`で適用する（`-m`による引数展開事故を避けるため）。

## 整理計画の提示

計画は以下1枚に収めて提示する。コミットへの参照は「A<番号>」「B<番号>」で行い、ハッシュは表に出さない。
確認事項には自動分類では判断できない点だけを並べる。無害かつ同種・同ディレクトリで自明なsquashは質問しない。

```text
整理計画

【元順序】
A1. feat: ユーザー認証を追加
A2. docs: READMEにセットアップ手順を追記
A3. refactor: 認証モジュールの関数を整理
A4. fix: NPE対策
A5. docs: CHANGELOG更新
A6. ci: workflowのタイムアウト延長

【新順序(4コミットに集約)】
B1. feat: ユーザー認証を追加 (A1+A3)
B2. fix: NPE対策 (A4)
B3. docs: READMEとCHANGELOGを更新 (A2+A5)
B4. ci: workflowのタイムアウト延長 (A6)

【確認事項】
1. A1にA3をまとめてよいか?(ペア例外)
2. 新順序の並びでよいか?
```

ルール

- 新順序の各行は`B<番号>. <統合後メッセージ草案> (<元番号の並列>)`の1行で書く
- 統合後メッセージ草案は原則新規起こし。片側採用する場合はその旨を行末に明記する
- 確認事項はYes/Noまたは選択式で答えられる形にする
- 全確認事項に判断が返ってくるまで実行フェーズに進まない

## 実行: 共通準備

```bash
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_REF="refs/tidy-backup/${TS}"
git update-ref "${BACKUP_REF}" HEAD
BRANCH="$(git symbolic-ref --short HEAD)"
```

退避refは`refs/tidy-backup/`の独自名前空間に作る。`refs/tags/`は`fetch.pruneTags=true`で消えうるし、`refs/heads/`は`git branch`一覧に紛れてユーザーが誤操作しうるため使わない。

元コミットのコンテンツハッシュを収集する。`git patch-id`は空白差分を無視するため使わず、インラインでSHA-256を計算する（シェル関数にしないのは`$1`などの位置パラメーターがスキル本文読込時に展開される副作用を避けるため）。

```bash
git log --reverse --format=%H "$BASE..${BACKUP_REF}" | while read sha; do
  hash=$(
    git diff --binary --no-color --no-renames --full-index \
      --src-prefix=a/ --dst-prefix=b/ "${sha}^..${sha}" \
      | sha256sum | cut -d ' ' -f 1
  )
  printf "%s %s\n" "$hash" "$sha"
done > /tmp/tidy-orig.txt
```

## 実行: 全統合ファストパス

適用条件（すべて満たす場合のみ）

- 整理対象範囲のコミットがひとつ残らず単一のsquashグループに入る
- 承認された新順序が`B1`のみ（他にコミットが残らない）

この条件下では後続コミットが存在しないため、`git reset --soft`を1回行うだけで畳める。

```bash
git reset --soft "$BASE"
git commit -F "$MSG_FILE"
```

残存コミットが1つでもある場合は適用不可。次のcherry-pick連鎖にフォールバックする（末尾から順に`reset --soft`を繰り返すと後続コミットを巻き込むため）。

## 実行: cherry-pick連鎖（標準経路）

ファストパスに該当しない全ケースで使う。detached HEAD上で承認済みの新順序を再構成し、最後にbranch refを付け替える。

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

### conflict発生時

- reorder起因: 該当箇所だけreorderを諦める。`git cherry-pick --abort`で巻き戻し、当該コミットを元の相対位置に戻して進行する。整理全体は中断しない
- squash起因: 自動解決を試みず即座に報告する。`git cherry-pick --abort`で中断し、退避refからロールバックして計画を再検討する
- `git checkout --theirs`/`--ours`による安易な解決は禁止。修正単位が壊れる

## 検証

整理後コミットのコンテンツハッシュを同じ手順で収集する。

```bash
git log --reverse --format=%H "$BASE..HEAD" | while read sha; do
  hash=$(
    git diff --binary --no-color --no-renames --full-index \
      --src-prefix=a/ --dst-prefix=b/ "${sha}^..${sha}" \
      | sha256sum | cut -d ' ' -f 1
  )
  printf "%s %s\n" "$hash" "$sha"
done > /tmp/tidy-new.txt
```

承認済み計画と突き合わせて以下を確認する。

- squashされない元コミットは、そのコンテンツハッシュが新側のいずれか1つと完全一致する（reorderされていてもよい）
- squashされた新コミットは、以下の手順で親基準を揃えた再現比較を行う
    1. 新コミットの実親を`git rev-parse`で取得する
    2. detached HEADでその親をcheckoutする
    3. 対応する元コミット群を`git cherry-pick --no-commit --allow-empty`で順次stageする。全部stageし終えたら`git commit --allow-empty -F <統合後メッセージファイル>`で1コミットに統合する（途中でcommitしてはならない）
    4. ステップ2と同じ手順でコンテンツハッシュ化し、新コミットのハッシュと完全一致することを確認する

検証用の一時操作はすべてdetached HEAD上で完結させる。退避refや作業ブランチには触れない。

最後にツリー全体の裏取りを行う。

```bash
git diff "${BACKUP_REF}..HEAD"
```

出力が空でなければ事故とみなしロールバックする。空である限り整理後の最終ツリーは整理前と完全一致しているため、`make test`などの追加実行は不要である。
中間コミットの状態は変わるため厳密にはbisect可能性に影響しうるが、最終状態の等価性が機械的に証明されている以上、本スキルの責務はここで完結する。

検証がすべて通ったら退避refを削除する。

```bash
git update-ref -d "${BACKUP_REF}"
```

## 失敗時の復旧

```bash
git cherry-pick --abort 2>/dev/null || true
git status
git stash --include-untracked 2>/dev/null || true
git checkout "$BRANCH"
git reset --hard "${BACKUP_REF}"
git stash pop 2>/dev/null || true
```

退避refは成功するまで削除しない。復旧後は失敗原因を報告して計画を再検討する。

## 絶対に避ける操作

修正単位を壊す直接原因になるため、以下は禁止する。

- NG: `git reset --hard`でworktreeごと巻き戻す（退避refからの復旧を除く）
- NG: `git reset --soft`でupstreamまで戻したあとの一括ステージと一括コミット
- NG: `git add -A`でまとめ直す（WIPコミット作成時のみ例外）
- NG: `git push --force`/`--force-with-lease`
- NG: `git rebase --root`
- NG: 退避ref作成前の操作
- NG: conflict時の`git checkout --theirs`/`--ours`による安易な解決
- NG: push済みコミットへの`git commit --amend`
- NG: `git rebase -i`の使用（Claude Codeのシステム指示と整合しないため）
- NG: `rerere`有効状態での無検証実行
