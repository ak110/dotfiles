# アーキテクチャ: Antigravityへの配布

## ホーム配下のファイルを編集する前の確認

### Antigravity向け配布

`pytools/_internal/antigravity_plugin.py`が`agent-toolkit-agy/`と`agent-basics-agy/`を生成し、
`scripts/sync_antigravity_plugin.py`とpost-applyが同じ同期処理を使う。
toolkit側には`plugin.json`、`skills/`、`agents/`、`rules/AGENTS.md`と追加の常時規範ファイル、
直下の`hooks.json`・`mcp_config.json`、Python実装とlockfileを含む。
basics側は独立した品質スキル集で、
実行基盤のhook・MCP・Python環境を含めずに利用できる。
各manifestの名前は元の`agent-toolkit`・`agent-basics`を保持し、品質スキルの所有と名前空間を保つ。
生成rootはGit追跡対象外で、`scripts/sync_generated_files.py`の最後に同期する。
無指定は両方を同期し、`scripts/sync_antigravity_plugin.py --plugin agent-basics`はbasicsだけを同期する。
生成した`agent-basics-agy/`を選べば、toolkitの有効化を伴わずに品質基準を利用できる。
Claude Code・Codex・Agent Plugins向けの既存3形式と原本の品質本文は、この生成処理で変更しない。

共通常時規範は行境界で分割し、各ファイルを24,000バイト以内に保つ。
生成器は合計20,000トークンの予算をUTF-8の2 bytes/token換算でチェックし、
超過時は配布前に失敗させる。
追加の分割ファイルは`trigger: always_on`でロードする。
プロジェクト独自の規範もホストの同じ予算を使うため、その総量は対象プロジェクトで確認する。

6イベントのhookは同じ生成rootの`scripts/atk-hook antigravity <イベント名>`を呼ぶ。
MCPは原本から`agents_server`・`pyfltr`を選び、root参照を絶対パスへ投影する。
スキル本文中の実行用rootも同じ絶対パスへ投影し、環境変数展開への依存を解消する。

post-applyはAntigravity CLI導入、Codex snapshotの生成とGemini設定反映の後に配布rootを同期する。
続いて`~/.gemini/config/plugins.json`の`entries`へ
作業ツリーの絶対パスと`include_only: ["agent-toolkit-agy", "agent-basics-agy"]`を追加する。
旧版が作成したtoolkitだけの管理対象entryは、同じentryを両方の指定へ更新する。
他の登録と継承設定を保持し、同じ登録は再追加しない。
プラグインの有効/無効を記録するユーザーの`config.json`は変更しない。
登録や更新後は新しいagyセッションで反映を確認する。

Antigravityのスキル本文にも24,000バイトの読込上限があるため、両プラグインで共通の生成処理が上限を超える
`SKILL.md`のfrontmatterを短いSKILL.mdに保持し、本文を
`references/antigravity-full-instructions.md`へ保存する。
短いSKILL.mdは起動時に全文資料を読む。
全文資料の相対参照は元のスキルディレクトリを起点に解決し、原本の必要本文と参照先を保持する。
`share/*.subagent.md`は入力契約を保持した原本として配送し、`agents/`に
対応する名前と原本の読込指示を持つnative agent定義を生成する。
