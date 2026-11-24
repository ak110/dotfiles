# アーキテクチャ: Antigravityへの配布

## Antigravity向け配布

`agent-toolkit-agy/`と`agent-basics-agy/`を`pytools/_internal/antigravity_plugin.py`が生成する。
basics側は独立した品質スキル集で、実行基盤のhook・MCP・Python環境を含めずに利用できる。
各manifestの名前は元の`agent-toolkit`・`agent-basics`を保持し、品質スキルの所有と名前空間を保つ。
生成rootはGit追跡対象外で、Claude Code・Codex・Agent Plugins向けの既存3形式と原本の品質本文は、この生成処理で変更しない。

常時読み込ませる規則（`rules/`）は生成しない。
agent-toolkitの規範は入口のスキル`agent-toolkit:atk-mode`と、その表から選ぶ手順書が持ち、SessionStartのhookが入口の起動を求める1行を出す。
入口を届ける経路は`agent-toolkit/skills/atk-mode/references/agent-documents.md`「入口を届ける経路」にある。
規則の生成とその容量予算を廃止した経緯は、削除前の撤去台帳`docs/development/removed-mechanisms-hooks.md`「常時規範と委譲のスキル」にある（読み方は[索引](index.md)）。

post-applyは`~/.gemini/config/plugins.json`の`entries`へ作業ツリーの絶対パスと`include_only`を追加し、他の登録と継承設定を保持する。
プラグインの有効/無効を記録するユーザーの`config.json`は変更しない。
登録や更新後は新しいagyセッションで反映を確認する。
MCPとスキル本文中の実行用rootは、環境変数展開への依存を避けるため、生成時に配布rootの絶対パスへ投影する。

Antigravityのスキル本文にも24,000バイトの読み込み上限があるため、生成処理は上限を超える`SKILL.md`のfrontmatterを短いSKILL.mdに保持し、本文を`references/antigravity-full-instructions.md`へ保存する。
短いSKILL.mdは起動時に全文資料を読む。
全文資料の相対参照は、保存先の`references/`ではなく元のスキルディレクトリを起点に解決する。
