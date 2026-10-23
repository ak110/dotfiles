# レーン統合の失敗の受領

本書は`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`に従ってレーン担当を起動したメインが、統合の返却として、統合先との組合せだけで生じた変更範囲の検証失敗の是正commitか、rebase進行中の競合を受け取ったときの扱いを定める。

是正commitを受領した場合は、失敗した検証が確かめる契約の送信側・受信側・実装・テストの差分と、再検証の終了コードを確かめる。警告はレーン担当が`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`「検証結果の警告の判定」で付けた判定と根拠を受け取り、処理の成立に影響しない既存の警告だけを理由に受理を求め直さない。確認したHEADを明記して同じthreadへ統合再開を指示し、契約外の差分か検証不足があれば不足箇所を返して是正を求める。

rebase進行中の競合を受領した場合は、次のとおり扱う。レーン担当に競合を解消させずに返させるのは、解消した差分がレビューを経ずに統合先へ入ることを防ぐためである。

1. 同じレーン担当threadへ競合の解消を指示する。別の修正担当は起動しない。
2. レーン担当は`agent-toolkit:process-wi`の`references/lane-integration-failure.md`「rebaseの競合」に従って解消し、`対応完了`または`対応完了（再レビュー不要）`を返す。
3. `対応完了`なら同じターンで`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「レビュー修正」と同じ判定で実行レビュー担当へ`再レビューせよ`と`round`を送る。複数の計画を持つレーンでは、送る計画を同書「起動方法別のレビュー基準」の影響する計画の判定で選ぶ。収束後に`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`「統合の指示と受領」に従って新しい`実行レビュー済みHEAD`で統合指示を再送する。`対応完了（再レビュー不要）`にも同書「レビュー修正」の証拠判定を適用し、再判定が不要な場合だけ、保持した修正前後OIDの対応を使って現在のHEADで統合指示を再送する。

同じthreadを継続できない場合は`agent-toolkit:delegation`のSKILL.mdの`## 継続と新規起動`に従い、新しいレーン担当には`再開位置`として`~/.claude/plans`の計画ファイルの絶対パスを渡す。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/lane-integration.subagent.md#「検証結果の警告の判定」` | 判断 | `section:agent-toolkit:*/share/lane-integration.subagent.md#「検証結果の警告の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `lane-integration-failure.md#「rebaseの競合」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/lane-integration-failure.md#「rebaseの競合」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-review.parent.md#「レビュー修正」` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#「レビュー修正」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.parent.md#「統合の指示と受領」` | 判断 | `section:agent-toolkit:*/share/exec.parent.md#「統合の指示と受領」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` |
