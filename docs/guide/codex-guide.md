# Codex利用ガイド

dotfiles利用者は`update-dotfiles`/`chezmoi apply`により、Codex向け設定も`~/.codex/`へ配布される。

## 推奨構成

配布内容は以下の構成とする。

- `~/.codex/AGENTS.md`: Codex向けの薄いアダプター。
  日本語・文体指定と、agent-toolkitルール・スキルを読む方針のみを記述する
- `~/.codex/agent-toolkit/rules`: Claude Code側のagent-toolkitルール原本へのシンボリックリンク
- `~/.codex/skills/*`: `agent-toolkit/skills/*`および`.chezmoi-source/dot_claude/skills/*`へのシンボリックリンク
- プロジェクト直下の`.agents/skills`: プロジェクト専用スキルディレクトリへのシンボリックリンク

CodexはClaude Codeの`CLAUDE.md`や`.claude/rules/`を同じ読み込み規則では扱わない。
そのため、常時ロードの入口は`AGENTS.md`へ集約し、本文は原本ファイルを参照する形にする。
ファイルコピーで同期すると改訂漏れが発生するため、共有対象はchezmoiの`symlink_*.tmpl`で配布する。

プロジェクト直下の`.claude/rules/`と`.claude/skills/`は、Claude Codeでは自動ロード・自動検出される。
Codexでは同じ挙動を前提にできないため、Codex側のプロジェクト専用スキルは`.agents/skills/`へ配置する。
`.claude/skills/`の原本を再利用する場合も、コピーせず`.agents/skills -> .claude/skills`のシンボリックリンクにする。
`.claude/rules/`はCodex側に対応する専用ディレクトリへ移さず、`~/.codex/AGENTS.md`から該当ファイルを読むよう指示する。

`~/.codex/rules`はCodexの承認ルール用ディレクトリであり、Claude CodeのMarkdownルールとは互換性がない。
agent-toolkitのMarkdownルールは`~/.codex/agent-toolkit/rules`に配置する。

プロジェクト固有設定は、原則として`AGENTS.md`を実体ファイル、
`CLAUDE.md`をClaude Codeのfile import記法`@AGENTS.md`を含むアダプターとして配置する。
両方を実体ファイルとすることで、コピー欠落やシンボリックリンク非対応環境での事故を回避する。
Codex専用の差分が必要な場合のみ、`AGENTS.md`本体に分岐記述を追加する。
