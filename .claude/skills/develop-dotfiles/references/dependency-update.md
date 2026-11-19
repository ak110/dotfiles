# 依存関係の更新

## 自動チェックと依存更新

1. `make update`を実行の候補にする前に、現行の`Makefile`の`update` targetと、それが呼ぶ子targetを読み、実際の処理対象に変更対象が含まれる場合だけ候補にする。
   現在の主な更新対象は`uv.lock`と`agent-toolkit/uv.lock`の依存である。
   lockの更新後にコンテナーでの依存の導入を検証し（`ci-deps-check`）、続けて`prek autoupdate`、`mise lock`、`pinact`で更新する。
   `rust/claude-statusline/Cargo.lock`も`Cargo.toml`の制約内で更新し、fmt・clippy・testを実行する。
2. GitHub Actionsのハッシュピンだけを更新する場合は`make update-actions`を実行する。
