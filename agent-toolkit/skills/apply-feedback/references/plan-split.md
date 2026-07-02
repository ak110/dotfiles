# 計画分割時の並列実装委譲

`agent-toolkit:apply-feedback`ステップ4「計画分割の判断基準」で2計画以上に分割した場合の
実装工程の並列化手順を定める。

## 委譲先と処理単位

- 委譲先ツール: `Agent`ツール
- サブエージェント種別: `subagent_type: claude`
- 起動形態: 個別foreground並列委譲
- 処理単位: 委譲先が`agent-toolkit:plan-impl`工程まで完遂する
- 起動プロンプト: `agent-toolkit:plan-mode`工程6で作成した計画ファイルパスを埋め込む
- 1計画に統合した場合: 並列委譲せずメイン側で順次実装する

## 合流点

`git push`と後始末（`dotfiles-fb adopt`または`dotfiles-fb reject`）は、
全計画完遂後にメイン側で一括実施する。
呼び出し元スキルの後始末順序を維持する。
