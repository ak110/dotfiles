# 委譲の保守資料

委譲の仕組みを保守する人だけが使う事実をまとめる。
エージェントが作業中に読む規範（`agent-toolkit:atk-mode/references/model-settings.md`など）からは外している。
設計の理由は[OpenCode CLI backend](design-agents-server-opencode.md)と[工程別モデル設定と待機](design-workflow-models-waiting.md)にある。

## OpenCodeのターン起動

- OpenCodeの各ターンは`opencode run --format json --auto`で実行し、モデルを`--model`、effortを`--variant`、継続するセッションIDを`--session`へ渡す。
- 委譲先へ届ける起動の本文、ユーザーの規範、plugin rootはプロンプトの本文へ渡し、必要なスキルは同じrootから読み込ませる。
- 実行中のターンへの追加の入力は、そのターンの終了を待ってから同じID宛てに送る。
- `orchestrate_model`には、保存値と環境変数のどちらでもOpenCodeを指定できない（`atk config`が拒否する）。

## 旧モデル設定の移行

- 段位制の9キーか用途別のengine順の5キーが保存されている場合は、初回の`atk config show`・`get`か候補の解決の時点で6工程の設定へ合成し、廃止キーを一括で削除する。
- 古い工程キーが共存していても、合成した値を優先し、`write_model`とモデル以外の設定は保つ。
- それ以前の旧旧4キーから段位制への移行処理は撤去済みである。
- 廃止キーに対応する環境変数がある場合は、工程別の新しい環境変数へ移す案内を出して止まる。
