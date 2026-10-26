# レーン統合の失敗の受領

本書は、`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`に従いレーン担当を起動したメインエージェントが、統合の処理結果として「統合先との組み合わせによって生じた変更範囲の検証失敗に対する是正コミット」や「rebase進行中の競合」を受け取った際の対処手順を定める。

是正コミットを受け取った場合は、失敗した検証が対象とする契約（送信側・受信側・実装・テスト）の差分と、再検証の終了コードを確認する。警告については、レーン担当が`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`「検証結果の警告の判定」で行った判定と根拠を受け入れる。処理の成立に影響しない既存の警告のみを理由として再度の受理確認を求めることは避ける。確認済みのHEADを明記して同一スレッドへ統合の再開を指示し、契約外の差分や検証不足が認められる場合は、不足箇所を伝えて修正を求める。

rebase進行中の競合を受け取った場合は、次の手順で対処する。レーン担当に競合をその場で解消させずに一旦報告させる理由は、解消時の差分がレビューを経ずに統合先へ取り込まれる事態を防ぐためである。

1. 同一のレーン担当スレッドへ競合の解消を指示する。別の修正担当を新規に起動することは避ける。
2. レーン担当は`agent-toolkit:process-wi`の`references/lane-integration-failure.md`「rebaseの競合」に従って競合を解消し、`対応完了`または`対応完了（再レビュー不要）`を返す。
3. `対応完了`が返った場合は、同一ターン内で`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「レビュー修正」と同様に判定し、実行レビュー担当へ`再レビューせよ`と`round`を送信する。複数の計画を含むレーンでは、同書「起動方法別のレビュー基準」の影響を受ける計画の判定に基づいて対象の計画を選択する。指摘の収束後、`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`「統合の指示と受領」に従い、新たに得られた`実行レビュー済みHEAD`を指定して統合指示を再送する。`対応完了（再レビュー不要）`の場合も同書「レビュー修正」の証拠判定を適用し、再度の判定が不要と確認できた場合に限り、保持している修正前後のOID対応関係を用いて現在のHEADで統合指示を再送する。

同一スレッドを継続できない場合は、`agent-toolkit:delegation`の`SKILL.md`「継続と新規起動」に従い、新規のレーン担当へ`再開位置`として`~/.claude/plans`配下にある計画ファイルの絶対パスを渡す。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` |
| 統合の返却で警告の判定と根拠を受け取る時 | `../../../share/lane-integration.subagent.md#「検証結果の警告の判定」` | 判断 | `section:agent-toolkit:*/share/lane-integration.subagent.md#「検証結果の警告の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `lane-integration-failure.md#「rebaseの競合」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/lane-integration-failure.md#「rebaseの競合」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-review.parent.md#「レビュー修正」` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#「レビュー修正」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.parent.md#「統合の指示と受領」` | 判断 | `section:agent-toolkit:*/share/exec.parent.md#「統合の指示と受領」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` |
