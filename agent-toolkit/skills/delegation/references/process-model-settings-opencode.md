# 工程別モデル設定：OpenCodeのターン起動

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## OpenCodeのターン起動

- OpenCodeの各ターンは`opencode run --format json --auto`で実行し、モデルを`--model`、effortを`--variant`、継続セッションIDを`--session`へ渡す。
  共通規範、委譲先規範、ユーザー規範、およびplugin rootをプロンプト本文へ渡し、必要なスキルは同rootから読み込む。
  実行中のターンへの追加メッセージ送信は、現在のターンの終了を待ってから同一ID宛てに送る
