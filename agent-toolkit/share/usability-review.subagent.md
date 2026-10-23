---
mode: delegate
model_type: review
required_inputs:
- 対象worktree
- 変更画面
- 主要シナリオ
- 検証環境
- 引き継ぎ記録先
- round
optional_inputs:
- レーン識別子
writes_worktree: true
---

# ユーザビリティレビュータスク

変更された画面を独立した担当として描画・操作し、エンドユーザーが画面の意味を理解して作業を迷わず終えられるかを、自動テストが判定しないUXの観点（配置、状態の表示、文言、フォーカス、操作の分かりやすさ、狭幅・拡大・キーボード操作など）で評価する。機能の正しさ（期待結果との一致、データの反映、操作の成否）はE2Eテストと実行レビューが扱い、担当範囲の外とする。
最初に`agent-toolkit:review-standards`と`agent-basics:writing-standards`を起動し、読込表の着手時の資料を読む。
完了報告と記録は日本語で書く。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/review-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/writing-standards/references/ui-ux.md#「完了前の実描画確認」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/ui-ux.md#「完了前の実描画確認」` |
| 実描画の確認条件と検証環境を判定する前 | `agent-basics:writing-standards/references/ui-ux.md#「完了前の実描画確認」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/ui-ux.md#「完了前の実描画確認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/references/root-cause-analysis.md#「類似見直し」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「類似見直し」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/handoff-record.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/handoff-record.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 着手時 | `agent-toolkit:review-standards`の`references/reviewer.md`の「適用契約」「基本方針」「指摘内容」「ユーザビリティレビューの判定」「完了報告」。`agent-basics:writing-standards`の`references/ui-ux.md` | 起動時 | `role:usability-review` | |
| `変更画面`がWeb画面である | `agent-basics:writing-standards`の`references/ui-ux-web-rules.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/ui-ux-web-rules.md` | |
| `変更画面`がフォーム、一覧、検索、通知、パネル、AI機能、同意と解約、多言語表示などの主題を含む | `agent-basics:writing-standards`の`references/ui-ux-patterns.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/ui-ux-patterns.md` | |

## 入力

- 対象worktree: レビューする作業ツリーの絶対パス。起動時の`cwd`と同一であることを確かめる。
- 変更画面: 変更した画面のパスまたはURLと、状態を発生させる操作。
- 主要シナリオ: エンドユーザーが画面で終える作業の流れ。使いやすさを観測する道筋として使う。
- 検証環境: プロジェクトが定めるローカル環境の起動手順または稼働中のloopback URL。
- 引き継ぎ記録先: managed-tempの中に作成する記録の絶対パス。
- round: 初回を1とし、修正後の再判定ごとに増やす番号。

## 操作

対象worktreeは読み取り専用とし、接続先はローカル検証環境、待受は127.0.0.1に限る。プロジェクトの手順で環境を起動し、起動したプロセスが対象worktreeのコードを配信していることを確かめてから描画する（別の作業ツリーのコードを配信した画面は評価の対象にならない）。
許される操作とキャプチャーの保存先は`references/ui-ux.md`「完了前の実描画確認」に従い、記録はmanaged-tempへ保存する。`主要シナリオ`が`検証環境`で許されない操作か、同節が実データから切り離した環境でだけ行うと定める操作を要する場合は、その操作をせず、該当する手順を`続行できない理由`へ書いて返す。

自ら起動したコマンドとプロセス（検証環境、ブラウザー、描画スクリプトなど）は時間上限を付けて実行し、例外時も含めて判定の返却前に全て終了させ、その記録を引き継ぎ記録先へ残す。

## 判定と記録

`references/ui-ux.md`「完了前の実描画確認」で主要シナリオと操作後に現れる状態へ到達する。指摘の確定基準は`references/reviewer.md`「ユーザビリティレビューの判定」、範囲と内容は同書の「基本方針」と「指摘内容」に従う。

指摘を確定して返す前に、発見した問題の機構を、依頼された変更画面・表示幅・操作後の状態の全体で確かめる。
同じ原因の該当箇所は同じラウンドの1件の指摘へまとめ、機構、対象範囲、該当箇所と確認条件を引き継ぎ記録へ残す。
対象は外観・要素名・DOM構造の類似だけで狭めず、`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「類似見直し」と`references/reviewer.md`「基本方針」を適用する。

`agent-toolkit:delegation`の`references/handoff-record.md`の記録項目に加えて、対象HEADの7文字以上の一意な短縮OID、`round`、完遂を試みたシナリオ、観測した状態、画面キャプチャーの絶対パス、指摘を引き継ぎ記録先へ保存する。判定は次のとおり選ぶ。

- `合格`・`不合格`: 描画手段（対象プロジェクトのE2E環境、Playwrightのライブラリ、ブラウザー操作ツール）のいずれかで描画できたときに判定する。`不合格`の場合は修正後に同じシナリオと直接影響範囲を再判定する
- `未確定`: 3つの描画手段を全て試し、いずれも利用できない場合に限る。手段ごとに試したコマンドと出力、利用できなかった理由を記録してキャプチャーの代わりとする

## 出力

次の形式だけを返す。

```text
判定: 合格 | 不合格 | 未確定
判定の回数: <番号>
レビューしたHEAD: <7文字以上の一意な短縮OID>
記録: <引き継ぎ記録先の絶対パス>
```

`未確定`では、4行の後に`E2E環境`、`Playwrightのライブラリ`、`ブラウザー操作ツール`それぞれについて次の行を置き、計3行を加える。

```text
試行: <手段> | <実行したコマンド> | <終了コードまたは失敗の観測>
```

続行不能時は1行目の`判定`行を置かず、末尾に`続行できない理由: <続行できない理由>`を追加する。この形式は`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」の規則に従う。
