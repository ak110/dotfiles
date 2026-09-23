---
name: hook-implementation
description: >
  Claude Code・Codexのhookを実装・編集するとき、及びhook間で共有するセッション状態の設計・変更時に起動する。
  hookの入出力契約、遮断と警告の選択、状態ファイルとフラグの記録元・利用先を扱う。
---

# Hookの実装とセッション状態

hookを実装・編集するときは`references/claude-hooks.md`を全文読む。
hook間で共有するセッション状態を設計・変更するときは`references/session-state-and-flags.md`も全文読む。
両資料は配布先のプラグイン利用者も参照する正本である。

著者向けの一般的な品質基準は`agent-toolkit:writing-standards`を併用する。
