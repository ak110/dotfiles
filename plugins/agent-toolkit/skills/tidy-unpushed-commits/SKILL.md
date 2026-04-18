---
name: tidy-unpushed-commits
description: 複数の未プッシュコミットを安全にsquash・reorder・メッセージ書き直しするスキル。退避refとツリー差分検証で最終ツリーの同一性を機械的に担保する。「未プッシュコミットを整理したい」「コミット履歴をきれいにしたい」「reorderしたい」「散らばったコミットをまとめ直したい」などの明示的指示でトリガーする。直前コミットへのamendや特定コミットへのfixupはagent.mdの指示で足りるため本スキルの対象外。
---

# 未プッシュコミットの整理

複数の未プッシュコミットのsquash/reorder/メッセージ書き直しを、退避refとツリー差分検証で最終ツリーの同一性を保証しながら行う。
`git reset --hard`による巻き戻し・一括ステージ・一括コミットなどの破壊的手順は採用しない（詳細は「絶対に避ける操作」節）。

## 適用条件と非適用条件

本スキルは複数コミットの再構成（squash/reorder/メッセージ書き直し）が必要な場合に使う。
直前コミットへの`git commit --amend`や特定コミットへの`git commit --fixup`で済む操作はagent.mdの既存指示に従い、本スキルを起動しない。

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

- `fetch.prune`/`fetch.pruneTags`はローカル限定の退避refも併せて削除しうるため、このfetchに限り明示的に無効化する
- 各コミットに`origin/<branch名>`が付いていればpush済みである。範囲から除外する
- `BASE`より先（古い側）は絶対に触らない
- `git rev-list --merges`の出力が空でなければ即中断して報告する
- `rerere.enabled`が`true`の場合はユーザーに警告してから進める（意図せぬ自動解決を避けるため）

## トリアージ（早期分岐）

前提確認の結果をもとに、以下の順でパターンを判定する。パターン1・2に該当する場合は本スキルの残りの手順を実行せず、agent.mdの既存指示に委ねて処理を完了する。

### パターン1: amend

条件（すべて満たす）:

- 未プッシュコミットが1つ以上存在する
- ユーザーの意図が「未コミット変更を直前コミットに吸収する」または「直前コミットのメッセージを修正する」と解釈できる
- 未プッシュコミット間のreorderやsquashの要望がない

対応:

1. worktree/indexに未コミット変更がある場合、`git diff`/`git diff --cached`で内容を提示し、amend対象と無関係な変更が混じっていないかユーザーに確認する（無関係な変更がある場合は分離を促す）
2. agent.mdのamendパターン（`git commit --amend`）で処理する

本スキルの以降の手順は実行しない。

### パターン2: fixup

条件（すべて満たす）:

- 未プッシュコミットが2つ以上存在する
- ユーザーが特定の未プッシュコミット（直前以外）を名指しで修正対象としている
- 未プッシュコミット間のreorderやsquashの要望がない

対応:

1. worktree/indexに未コミット変更がある場合、`git diff`/`git diff --cached`で内容を提示し、fixup対象と無関係な変更が混じっていないかユーザーに確認する（無関係な変更がある場合は分離を促す）
2. agent.mdのfixupパターン（`git commit --fixup` + `GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash`）で処理する

本スキルの以降の手順は実行しない。

### パターン3: フル整理

パターン1・2に該当しない場合。複数コミットのsquash・reorder・メッセージ一括書き直しなどが必要なケース。以降の手順（uncommitted changesの取り込み → コミットの分類 → 整理計画 → 実行 → 検証）を実行する。

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

怪しいカテゴリのコミットが1件でも存在する場合、全件の確認回答を受け取ってから整理計画の提示に進む。

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

【新順序（4コミットに集約）】
B1. feat: ユーザー認証を追加（A1+A3）
B2. fix: NPE対策（A4）
B3. docs: READMEとCHANGELOGを更新（A2+A5）
B4. ci: workflowのタイムアウト延長（A6）

【確認事項】
1. A1にA3をまとめてよいか?（ペア例外）
2. 新順序の並びでよいか?
```

ルール

- 新順序の各行は`B<番号>. <統合後メッセージ草案> (<元番号の並列>)`の1行で書く
- 統合後メッセージ草案は原則新規起こし。片側採用する場合はその旨を行末に明記する
- 確認事項はYes/Noまたは選択式で答えられる形にする
- 全確認事項に判断が返ってくるまで実行フェーズに進まない
- 全コミットが無害カテゴリに分類され、怪しいコミットが0件かつペア例外の候補も0件の場合、確認事項は「新順序の並びでよいか」の1点に簡略化してよい

## 実行: 共通準備

```bash
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_REF="refs/tidy-backup/${TS}"
git update-ref "${BACKUP_REF}" HEAD
BRANCH="$(git symbolic-ref --short HEAD)"
```

退避refは`refs/tidy-backup/`の独自名前空間に作る。`refs/tags/`は`fetch.pruneTags=true`で消えうるし、`refs/heads/`は`git branch`一覧に紛れてユーザーが誤操作しうるため使わない。

## 実行: 経路選択

承認済み新順序の形に応じて2経路を使い分ける。

- 全統合ファストパス: 整理対象範囲の全コミットが単一のsquashグループに入り、新順序が`B1`のみの場合。`git reset --soft`1回で畳める
- cherry-pick連鎖: 上記以外の全ケース。detached HEAD上で新順序を再構成し、最後にbranch refを付け替える

経路ごとの具体コマンド・squashグループ合流の詳細・conflict発生時の対応は`references/cherry-pick.md`を参照する。

## 検証

ツリー全体の差分を確認する。

```bash
git diff "${BACKUP_REF}..HEAD"
```

出力が空でなければ事故とみなしロールバックする。空である限り整理後の最終ツリーは整理前と完全一致しているため、`make test`などの追加実行は不要である。
中間コミットの状態は変わるため厳密にはbisect可能性に影響しうるが、最終状態の等価性が証明されている以上、本スキルの責務はここで完結する。

検証が通ったら退避refを削除する。

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
- NG: 退避ref作成前に実行フェーズ（cherry-pick・reset等のコミット変更操作）を開始する
- NG: conflict時の`git checkout --theirs`/`--ours`による安易な解決
- NG: push済みコミットへの`git commit --amend`
- NG: フル整理パスでの`git rebase -i`の使用（cherry-pick連鎖で代替する）
  - 例外: トリアージでfixup委譲する場合のagent.md準拠の非対話`GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash`は対象外
- NG: `rerere`有効状態での無検証実行
