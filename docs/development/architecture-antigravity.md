# アーキテクチャ: Antigravityへの配布

## Antigravity向け配布

`agent-toolkit-agy/`と`agent-basics-agy/`を`pytools/_internal/antigravity_plugin.py`が生成する。
basics側は独立した品質スキル集で、実行基盤のhook・MCP・Python環境を含めずに利用できる。
各manifestの名前は元の`agent-toolkit`・`agent-basics`を保持し、品質スキルの所有と名前空間を保つ。
生成rootはGit追跡対象外で、Claude Code・Codex・Agent Plugins向けの既存3形式と原本の品質本文は、この生成処理で変更しない。

共通常時規範は行境界で分割し、各ファイルを24,000バイト以内に保つ。
生成器は合計20,000トークンの予算をUTF-8の2 bytes/token換算でチェックし、
超過時は配布前に失敗させる。
失敗した場合だけ、特定の場面で使う内容をその判断・操作の前に読む資料へ移し、
整理後の目安17,000トークン以下まで下げる。
上限以下では、容量を理由に追加の測定・記録・削減を求めない。
整理後の値は生成の受理上限ではなく、通常の追記を数回吸収するためエージェントが定めた目安である。
換算・目安の根拠と再確認手順は[常時規範の容量監査](audit-records-antigravity.md#pytools_internalantigravity_pluginpy常時規範の容量2026年11月12日)にある。
文書記述量の判断は[過剰設計の抑制](concepts-overengineering.md#過剰設計の抑制)と
`agent-basics/skills/writing-standards/references/agent-documents-additions.md`の容量検査の規定へ対応する。
プロジェクト独自の規範もホストの同じ予算を使う。
本生成器の検査対象はtoolkitの共通常時規範である。

post-applyは`~/.gemini/config/plugins.json`の`entries`へ作業ツリーの絶対パスと`include_only`を追加し、他の登録と継承設定を保持する。
プラグインの有効/無効を記録するユーザーの`config.json`は変更しない。

Antigravityのスキル本文にも24,000バイトの読込上限があるため、生成処理は上限を超える`SKILL.md`のfrontmatterを短いSKILL.mdに保持し、本文を`references/antigravity-full-instructions.md`へ保存する。
