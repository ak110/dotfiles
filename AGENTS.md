# AGENTS.md: dotfiles

このリポジトリはchezmoiで管理するdotfilesリポジトリであり、`.chezmoi-source/`配下のファイルを`~/.*`へ展開する。
多数の小さなコマンドラインツールと、Claude Code・Codex向けの共有設定（プラグイン`agent-toolkit`と`agent-basics`）も持つ。
成果物の品質は`agent-basics`、WI・委譲・認可・完了報告の運用は`agent-toolkit`に従う。
本リポジトリのコマンド（`update-dotfiles`など）は、ユーザー本人が自分の環境で実行する道具である。
設計、実装、レビューの脅威モデルに、同じユーザーの悪意あるプロセスとUACを越える攻撃は含めない。

Pythonは、標準ライブラリだけで済む簡単な確認には`python3`を、リポジトリの依存やモジュールを使う処理には`uv run --frozen python`を使う。
規範にある`python -c`は起動の形の例であり、実行ファイル`python`があることは保証しない。

スキルや手順の使い方を尋ねられただけの時は、案内で終え、作業を始めない。

## 本リポジトリのスキル

`.claude/skills/`配下の、本リポジトリ専用の非配布スキルである。
作業に合うスキルを起動し、スキルの入口の表から資料を選ぶ。

| 作業 | スキル |
| --- | --- |
| テスト、整形、依存の更新、検証の範囲と実行、作業ツリーの準備、コミットメッセージの方針、ホスト本体のバイナリの検索 | `develop-dotfiles` |
| 本リポジトリを対象に含むprocess-wiの巡回の所要時間の目標と終了条件 | `develop-dotfiles`の`references/process-wi-duration.md` |
| `develop`と`master`のリリース運用、日次リリースの判定と実施 | `release-dotfiles` |
| PRのマージ、マージ後のブランチの同期、CIと必要なリリースの検収 | `merge-pr` |
| `agent-toolkit/`・`agent-basics/`と`.claude-plugin/marketplace.json`の編集、版数の更新、権限設定の配置 | `agent-toolkit-edit` |
| エージェント向け文書の改訂、変更した規範の自セッションへの適用、`プロジェクト規範の指定`、規範の削除・縮小 | `edit-dotfiles-norms` |
| ロールとファイル群の対応、配布元と配布先の対応 | `dotfiles-repo-layout` |
| `pytools/`、`scripts/`、`libexec/`、`bin/`、`rust/`の配置と実装 | `pytools-edit` |
| LinuxとWindowsのペアファイルの同期 | `sync-platform-pair` |

## 記録の所在

経緯、根拠、構造の記録は`docs/development/index.md`から選ぶ。
主な索引は、構成の`architecture.md`、運用の`operations.md`、設計の`design.md`、確定した方針の`concepts.md`、内部仕様の`internals.md`である。
障害と撤去した仕組みの経緯はcommit本文にあり、`git log --grep`で引く。
各規範の条文が根拠とする検証の日付、版数、再検証の手順は`docs/development/audit-records.md`にある。
