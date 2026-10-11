# 投稿前レビュー担当の起動と受領

```text
起動対象: external-write-review.subagent.md
```

`agent-toolkit:external-write-review`の対象となる文面を投稿するエージェントが、本書に従って投稿前レビュー担当を起動し、結果を受領する。
レビュー担当の観点と返却形式は`${CLAUDE_PLUGIN_ROOT}/share/external-write-review.subagent.md`が定める。

## 起動

確定した文面をUTF-8のファイルへ保存し、`agents_server`の`start`を次の引数で呼んで1件起動する。委譲先として投稿するエージェントも同じく起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 文面が根拠とする対象リポジトリの絶対パス |
| `subagent_md_path` | `external-write-review` |
| `extra_params` | 次の名前付き入力だけ |

- `文面ファイル`: 文面ファイルの絶対パス
- `投稿先と目的`: 投稿先と投稿の目的
- `根拠の所在`: 文面が根拠とする差分・観測結果・関連ファイルの所在

## 受領

結果は引数なしの`atk agents wait`で受け取る。指摘の採否は`agent-toolkit:review-standards`の`references/reviewee.md`に従って確定し、採用した指摘を文面へ反映してから投稿する。
レビューは投稿前に1回を原則とし、反映で事実主張や対象範囲が変わった場合だけ再レビューの要否を判断する。

レビュー結果を得られない場合（結果を返さない終端が同じ入力での再起動後も続く、`start`も同等の読み取り専用の委譲手段も使えない）は投稿を止める。原因と同等のレビュー手段を調べて認可内で回復できれば自ら再開し、レビュー省略や公開結果の変更に追加認可・選好が要る場合だけ、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」に従って文面と理由を示して確認する。
