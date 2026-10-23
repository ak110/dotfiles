# ユーザビリティレビューの起動と検収

```text
起動対象: usability-review.subagent.md
```

画面差分がある場合の並列画面レビュー（いずれも定義は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`「用語」）で、メインがユーザビリティレビュー担当を起動し、返却を検収する手順を定める。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `workflow-phases.md#「用語」` | 判断 | `section:agent-toolkit:*/share/workflow-phases.md#「用語」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/writing-standards/references/ui-ux.md#「完了前の実描画確認」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/ui-ux.md#「完了前の実描画確認」` |
| 実描画の確認条件と検証環境を決める前 | `agent-basics:writing-standards/references/ui-ux.md#「完了前の実描画確認」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/ui-ux.md#「完了前の実描画確認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/base-contract.md#「引き継ぎ記録先」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「引き継ぎ記録先」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md#「認可を要する操作」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「認可を要する操作」` |

## 起動

起動の前に、対象worktreeの書込主体（レーン工程ではレーン担当）へ、対象リポジトリの手順で対象HEADを再配備または起動させ、完了を確かめる。実装途中に配備済みの環境も対象HEADへ更新し、修正後の再判定でも新しい対象HEADで同じく準備する。

`agents_server`の`start`を次の引数で呼ぶ。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象worktreeの絶対パス |
| `subagent_md_path` | `usability-review` |
| `extra_params` | 次の名前付き入力 |

- 対象worktree: `cwd`と同じ絶対パス。
- 変更画面: 差分が変更した画面のパスまたはURLと、操作で表示する状態へ到達する手順。
- 主要シナリオ: 計画またはWIが指定するエンドユーザーの作業の流れと、そこで確かめるUX上の観点。期待結果やデータの反映、操作の成否といった機能の正しさは判定基準として渡さない。
  観点は変更画面ごとに、その画面に対するWIの完成条件と計画の受入シナリオが求める範囲に限り、完成条件が異なる画面はシナリオの文を分ける（計画の要約が完成条件より広い結果を述べていてもWIの完成条件に合わせる）。
  `agent-basics:writing-standards/references/ui-ux.md`「完了前の実描画確認」が実データから切り離した環境でだけ行うと定める操作を要する観点は、切り離した`検証環境`と許される操作を書くか、観点から外す。`検証環境`で禁止した操作を要する観点は書かない。
- 検証環境: 対象プロジェクトの手順から確定したローカル起動形または127.0.0.1の稼働中URL。
- 引き継ぎ記録先: 省略と継続の扱いは`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う。
- round: 初回は1、同じ担当への再判定では直前の番号に1を足す。
- レーン識別子: レーンの作業を対象とする起動に限り、そのレーンの識別子。session識別名を`<レーン識別子>-usability-review`にする。

## 返却後

返却された記録が対象HEADと`判定の回数`を示すか確かめる。`合格`と`不合格`では完遂を試みたシナリオ、観測状態とキャプチャーの実在を確かめる。`未確定`では`E2E環境`、`Playwrightのライブラリ`、`ブラウザー操作ツール`の3つの`試行:`行がそろうかを確かめ、欠ければ受理せず同じthreadへ欠けた手段の試行と再判定を求める。

`不合格`は根拠がその画面に対するWIの完成条件の範囲の内か外かで扱いを分ける。

- 範囲外の観察: 判定の根拠から外し、範囲を示して同じthreadへ再判定を依頼する。観察がエンドユーザーの停止場面か誤った結果を示す不足なら、`agent-toolkit:user-confirmation-and-report`の`SKILL.md`「認可を要する操作」の範囲外の不足の区分で判定する。バグの修正はprocess-wiの同じ実行の中で是正し、新しい挙動の追加は同書「確認を要する事項」の確認へ送る。それ以外は参考記録とする
- 範囲内の根拠: 指摘の機構と対象範囲、画面状態、操作順、表示幅と観測結果を修正条件として同じ実装担当（レーン工程では同じレーン担当）へ返す。担当は指摘箇所に加えて同じ機構を持つ依頼対象の全体を確認し、新しい対象HEADを実ブラウザーで描画する。対象全体について試した条件と実際の表示結果が再提出され、同じ機構の該当箇所へ修正が届いたことを確かめてから、同じユーザビリティレビュー担当threadへの再判定と実行レビュー担当への再レビューを同じHEADで並列に依頼する

統合前に、同じ対象HEADについてユーザビリティレビューの`合格`または`未確定`と、実行レビューの未解決指摘0件を確かめ、ユーザビリティレビューの記録を計画の検証結果へ加える。`未確定`では利用できなかった描画手段と未確認の画面状態を完了報告へ記す。
