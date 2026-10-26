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

変更された画面を独立した担当者として実際に描画・操作し、エンドユーザーが画面の意味を正しく理解して迷わずに作業を完了できるかを評価する。自動テストでは検出できないUX上の観点（配置、状態表示、文言、フォーカス移動、操作の分かりやすさ、狭い表示幅や拡大表示、キーボード操作など）を評価対象とする。期待結果との一致、データの反映、操作の成否といった機能的な正しさについては、E2Eテストおよび実行レビューが担当するため、本タスクの評価範囲からは除外する。
作業着手時には、まず`agent-toolkit:review-standards`と`agent-basics:writing-standards`を起動し、読込表に指定された着手時の資料を参照する。
完了報告と作成する記録は日本語で記述する。

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

- 対象worktree: レビュー対象とする作業ツリーの絶対パス。起動時の`cwd`と同一であることを確認する。
- 変更画面: 変更が加えられた画面のパスまたはURLと、該当する表示状態を発生させる操作手順。
- 主要シナリオ: エンドユーザーが画面上で完了させる一連の作業の流れ。使いやすさを観察するための基準経路として用いる。
- 検証環境: プロジェクトが定めているローカル環境の起動手順、または稼働中のloopback URL。
- 引き継ぎ記録先: managed-temp配下に作成する記録ファイルの絶対パス。
- round: 初回を1とし、修正後の再判定ごとに1ずつ増やす番号。

## 操作

対象worktreeは読み取り専用として扱い、接続先はローカルの検証環境、待ち受けアドレスは127.0.0.1に限定する。プロジェクトの手順に沿って環境を起動し、起動したプロセスが対象worktreeのコードを正しく配信していることを確認した上で画面を描画する（別の作業ツリーのコードを配信している画面は評価の対象としない）。
許可される操作およびキャプチャーの保存先は、`references/ui-ux.md`「完了前の実描画確認」に従い、取得した記録はmanaged-temp配下へ保存する。`主要シナリオ`に`検証環境`で許可されていない操作が含まれる場合や、同節において実データから切り離された環境でのみ実施すると定められた操作を要する場合は、その操作の実行を見合わせ、該当する手順を`続行できない理由`に記載して返却する。

自身が起動したコマンドおよびプロセス（検証環境、ブラウザ、描画スクリプトなど）には必ず実行時間の上限を設定する。例外的な終了時も含め、判定を返却する前にこれらをすべて確実に終了させ、その記録を引き継ぎ記録先へ残す。

## 判定と記録

`references/ui-ux.md`「完了前の実描画確認」に沿って、主要シナリオおよび操作後に現れる各表示状態へ到達する。指摘の確定基準は`references/reviewer.md`「ユーザビリティレビューの判定」に従い、指摘の範囲と内容は同書の「基本方針」および「指摘内容」に従う。

指摘を確定して返却する前に、発見した問題の発生機構について、依頼された変更画面、表示幅、操作後の状態の全体にわたって確認する。
同一の原因に起因する該当箇所は、同じラウンドにおける1件の指摘として集約し、問題の機構、対象範囲、該当箇所、確認条件を引き継ぎ記録へ記録する。
対象の絞り込みにあたっては、外観、要素名、DOM構造の表面的な類似性だけで判断せず、`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「類似見直し」および`references/reviewer.md`「基本方針」を適用する。

`agent-toolkit:delegation`の`references/handoff-record.md`で規定されている記録項目に加え、対象HEADの7文字以上の一意な短縮OID、`round`、完遂を試みたシナリオ、観測された状態、画面キャプチャーの絶対パス、指摘事項を引き継ぎ記録先へ保存する。判定結果は次のように選択する。

- `合格`・`不合格`: いずれかの描画手段（対象プロジェクトのE2E環境、Playwrightのライブラリ、ブラウザー操作ツール）を用いて画面を描画できた場合に判定する。`不合格`となった場合は、修正後に同一シナリオおよび直接影響範囲について再判定する。
- `未確定`: 3つの描画手段をすべて試行し、いずれも利用できなかった場合に限り選択する。手段ごとに試行したコマンド、実行出力、利用できなかった理由を記録し、画面キャプチャーの代用とする。

## 出力

次の形式のみを返す。

```text
判定: 合格 | 不合格 | 未確定
判定の回数: <番号>
レビューしたHEAD: <7文字以上の一意な短縮OID>
記録: <引き継ぎ記録先の絶対パス>
```

判定が`未確定`の場合は、上記4行の後に`E2E環境`、`Playwrightのライブラリ`、`ブラウザー操作ツール`のそれぞれについて次の行を配置し、計3行を追加する。

```text
試行: <手段> | <実行したコマンド> | <終了コードまたは失敗の観測>
```

作業を続行できない場合は、1行目の`判定`行を配置せず、末尾に`続行できない理由: <続行できない理由>`を追加する。この形式は`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」の規定に従う。
