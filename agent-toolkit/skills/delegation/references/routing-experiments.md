# 委譲の要否判定と委譲単位の確定：実行結果の検証

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 実行結果の検証

- 応答内容自体を必要とせず、実機確認や対照実験、識別子の観測のみを目的としてエージェントCLI（`claude -p`や`codex exec`など）や`agents_server`の委譲を起動する場合がある。
  このとき、検証対象がモデルやeffort、設定、ツールによって左右されない限り、軽量工程向けのモデル別名とeffort `low`を指定する。
  モデルはバージョン付きの古いIDに固定しない。
  `agents_server`では`model_type`に`claude:haiku/low`か`codex:luna/low`を渡す。
  検証対象が`delegate`や`task`の起動そのものでない限り、軽量起動である`explore`または`shell`の`mode`で起動する。
  CLIを直接起動する場合はセッションを保存せず、設定・ツール・MCPを読み込まず、最小限のシステムプロンプトで起動する。
  Claude Codeでは`--model haiku --effort low`に加えて`--no-session-persistence --setting-sources "" --tools ""`を指定する。
  さらに`--strict-mcp-config --disable-slash-commands --system-prompt <文>`も指定する。
  プロンプトは`--`の後ろに配置する（`--tools ""`が後続の位置引数を値として消費してしまうのを防ぐため）。
  なお`--bare`はOAuth認証を読み込まない。
  Codexでは`-c model_reasoning_effort=low --ignore-user-config --ignore-rules`を指定し、記録が不要な場合は`--ephemeral`も加える。
  `codex exec`はChatGPT認証において系列名`luna`を拒否するため、直接起動するときは`luna`系列の最新の完全なIDを指定する。
  起動回数は判定に必要な最小限に抑える。
  監査記録は`docs/development/audit-records-investigation.md`の「agent-toolkit/share/investigation-operations.md：実行結果の検証：2026年10月18日（2）」にある。
