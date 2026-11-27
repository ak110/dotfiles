# AGENTS.md: dotfiles

dotfilesは、chezmoiで `.chezmoi-source/` を `~/.*` に配布し、小さなCLIと、Claude Code・Codexで共有するプラグインを提供する。
スキルや手順の使い方だけを尋ねられたら、案内して終える。
作業を求められた場合は次の分担で進める。

- 成果物の品質は `agent-basics`、WI・委譲・認可・完了報告の手順は `agent-toolkit` に従う。
- dotfiles固有の操作は下表から選ぶ。
  コマンド（`update-dotfiles` など）は本人が自分の環境で使うため、設計・実装・レビューの脅威モデルに同じユーザーの悪意あるプロセスやUACを越える攻撃を含めない。
- 標準ライブラリだけで済む簡単な確認には `python3`、リポジトリの依存やモジュールを使うなら `uv run --frozen python` で起動する。
  規範の `python -c` は呼び出し方の例であり、実行ファイル `python` の存在を前提にしない。

## 本リポジトリのスキル

`.claude/skills/`配下の、本リポジトリ専用の非配布スキルである。
変更する対象に合うスキルと、実行する操作に合うスキルを起動する。
個々の手順は各スキルから選ぶ。

| 作業 | スキル |
| --- | --- |
| テスト、整形、依存の更新、検証の範囲と実行、作業ツリーの準備、コミットメッセージの方針、ホスト本体のバイナリの検索 | `develop-dotfiles` |
| 本リポジトリを対象に含むprocess-wiの巡回の所要時間の目標と完了条件 | `develop-dotfiles`の`references/process-wi-duration.md` |
| `develop`と`master`のリリース運用、周期に沿ったリリースの判定と実施 | `release-dotfiles` |
| PRのマージ、マージ後のブランチの同期、CIと必要なリリースの検収 | `merge-pr` |
| `agent-toolkit/`・`agent-basics/`と`.claude-plugin/marketplace.json`の編集、版数の更新、権限設定の配置 | `agent-toolkit-edit` |
| エージェント向け文書の改訂、変更した規範の自セッションへの適用、`プロジェクト規範の指定`、規範の削除・縮小 | `edit-dotfiles-norms` |
| ロールとファイル群の対応、配布元と配布先の対応 | `dotfiles-repo-layout` |
| `pytools/`、`scripts/`、`libexec/`、`bin/`、`rust/`の配置と実装 | `pytools-edit` |
| LinuxとWindowsのペアファイルの同期 | `sync-platform-pair` |
| `atk` のCLIを変更し、実際のコマンドで確かめる | `verify` |
| private-notesのGit同期、WIの操作承認の競合を検証する | Git同期は `git-sync-interleavings`、操作承認は `operation-approval-interleavings` |

## 記録の所在

経緯、根拠、構造の記録は`docs/development/index.md`から選ぶ。
主な索引は、構成の`architecture.md`、運用の`operations.md`、設計の`design.md`、確定した方針の`concepts.md`、内部仕様の`internals.md`である。
障害と撤去した仕組みの経緯はcommit本文にあり、`git log --grep`で引く。
各規範の条文が根拠とする検証の日付、版数、再検証の手順は`docs/development/audit-records.md`にある。
