---
name: external-write-review
user-invocable: false
description: >
  PR・MR・イシューの作成と本文編集、コメント、レビュー返信、Release本文、チャットやメールへのMCP経由の送信など、第三者が読む外部サービスへエージェントが起草または加筆した人間向けの文面を送る直前に起動する。
---

# 外部サービスへの投稿前レビュー

第三者に届く文面は、投稿する前に、起草者とは独立した読み取り専用の担当者に検証させる。
対象かどうかの判定、レビューの依頼、指摘の採否、および実際の投稿は、投稿を担当するエージェントが行う。投稿操作に必要な認可は`agent-toolkit:user-confirmation-and-report`に従って判定する。

投稿前レビューは、独立した担当者を起動できる手段とプラグイン全体の参照資料がそろっている環境で実施する。担当者を起動できない環境では、提示された文面と根拠を読み、検討結果を下書きとして返す。独立したレビューと認可を経た投稿はClaude CodeまたはCodexで行い、下書きの説明には[文章の品質基準](references/quality-standards.md)を用いる。

## 名指し参照の読込方法

本文で各参照の適用条件を確認し、選んだ資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の行に記載された資料は、起動本文とともに配送される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/external-write-review/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../review-standards/references/reviewee.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/references/reviewee.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |

## 対象

第三者が読む外部サービスに向けて、エージェントが起草または加筆した人間向けの文面を対象とする。
ユーザーから逐語で渡された文面、git push、およびコミットメッセージ（`agent-toolkit:commit`）は対象外とする。
private-notesのWI本文も対象外であり、これらは`agent-toolkit:wi-standards`と`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`に従う。

## 投稿前レビュー担当の起動と受領

```text
起動対象: external-write-review.subagent.md
```

`agent-toolkit:external-write-review`の対象となる文面を投稿するエージェントは、本書に従って投稿前レビュー担当を起動し、その結果を受け取る。
レビュー担当の確認観点と返却形式は、`${CLAUDE_PLUGIN_ROOT}/share/external-write-review.subagent.md`で定めている（所在参照）。

### 起動

確定した文面をUTF-8ファイルとして保存し、`agents_server`の`start`を次の引数で呼び出して1件起動する。委譲先として投稿するエージェントも同様に起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 文面が根拠とする対象リポジトリの絶対パス |
| `subagent_md_path` | `external-write-review` |
| `extra_params` | 次の名前付き入力だけ |

- `文面ファイル`: 文面ファイルの絶対パス
- `投稿先と目的`: 投稿先と投稿の目的
- `根拠の所在`: 文面が根拠とする差分・観測結果・関連ファイルの絶対パス、またはcommit、run ID、PR番号など対象を一意に取得できる識別子。レビュー担当自身も、読み取り専用のコマンド（`git show`、リモートと同期しない`atk wi show --skip-pull`など）やMCPの読取ツールを使って根拠を取得できる。同じ観測時点を固定する必要がある資料は、ファイルに保存して渡す

投稿予定の文面は、読み取り可能なUTF-8の通常ファイルに保存して固定する。
保存した根拠は絶対パスまたはJSON配列で渡し、識別子を指定する場合は取得先を添える。
レビュー担当は同じ対象・版の根拠を取得し、文面の主張と根拠が正しく対応しているか検収する。

### 受領

結果は引数なしの`atk agents wait`で受け取る。指摘の採否は`agent-toolkit:review-standards`の`references/reviewee.md`に従って確定し、採用した指摘を文面に反映してから投稿する。
レビューは投稿前に1回実施することを原則とし、反映によって事実の主張や対象範囲が変わった場合に限って、再レビューが必要かどうかを判断する。

レビュー結果を得られない場合（同じ入力で再起動しても結果を返さずに終了し続ける、あるいは`start`や同等の読み取り専用の委譲手段が使えない場合）は、投稿を中断する。原因と同等のレビュー手段を調査し、認可の範囲内で復旧できる場合は自ら処理を再開する。レビューの省略や公開結果の変更に追加の認可やユーザーの選好が必要となる場合に限り、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」に従い、文面と理由を示してユーザーに確認する。
