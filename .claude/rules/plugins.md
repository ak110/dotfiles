---
paths:
  - "plugins/**"
  - ".claude-plugin/marketplace.json"
---

# Claude Code plugin 編集チェックリスト

本リポジトリ配下の `plugins/` と `.claude-plugin/marketplace.json` を編集するときに確認する。
バージョン更新・SSOT同期・ドキュメント同期が別の場所に散らばっていて漏れやすいため、
ここに集約している。

## なぜ必要か

プラグインは `claude plugin update` 経由で配布される。
バージョン番号を上げない限り既存ユーザーへ更新は配信されない。
(同じバージョンでは `update` コマンドが「最新です」と返す)
過去に `agent-toolkit` (旧 `edit-guardrails`) で実害があったため、編集のたびにバージョン更新の要否を判定する。

また、バージョン情報はSSOT違反の状態で2ファイルに重複している。
片方だけ更新すると配布に失敗するため、必ず両方を同期する。

## SSOT の 2 ファイル

プラグインごとに以下の2箇所で `version` / `description` を完全に同一文字列に保つ。

- `plugins/<plugin-name>/.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json` の `plugins[]` 内 `name == "<plugin-name>"` のエントリ

整合性は各プラグインのテストで検査する。
`agent-toolkit`の担当は`TestManifestSsot`で、`make test`で自動的に失敗する
(場所: `plugins/agent-toolkit/tests/pretooluse_test.py`)。
deprecatedの空シェル`edit-guardrails`もmarketplace.jsonにエントリが残っているが、SSOTテストは`agent-toolkit`側のみで検証する。
新しいプラグインを追加するときは同等のSSOTテストも追加する。

## バージョン更新が必要な変更

ユーザーに届く振る舞いが変わるもの。以下のいずれかに該当する場合は必ずバージョンを更新する。

- プラグインのhookスクリプトやentry pointのロジック変更
- 新しいcheck / 機能の追加、既存checkの削除
- `hooks/hooks.json` など設定ファイルのmatcher / command変更
- 依存や実行環境要件の変更 (`requires-python` / script headerのdependencies)
- ブロック条件の緩和（false positive対策でallowlistを増やす等）

## バージョン更新が不要な変更

- コメント・docstringのみの修正
- `tests/` のみの追加・修正（SSOTテスト自身の変更を含む）
- 入出力が完全に不変なリファクタリング
- 誤字修正・スタイル調整

判断に迷う場合はバージョンを更新する方針とする（pre-1.0であれば頻繁にMINORを更新しても問題ない）。

## バージョン更新指針 (pre-1.0 `0.x.y`)

- 機能追加 / 検出範囲拡大 / descriptionが変わる規模の変更 → MINOR（`x` を +1し、`y` を0に戻す）
- 既存挙動のバグ修正 / 検出漏れの修正 → PATCH (`y` を +1)

破壊的変更の概念は1.0までは考慮しない。

## 同期先ドキュメント

`docs/guide/claude-code-guide.md` の「agent-toolkit」セクションに各プラグインのチェック内容要約がある。
以下の変更をしたときはここも併せて更新する（更新忘れが起きやすいのでここに明記する）。

- 新しいcheckの追加・既存checkの削除
- 検出範囲の大きな変更（allowlist / blocklistの方針変更）
- 依存ツールの変更（`uv` 以外を要求するようになった等）
- 新しいプラグインを追加した場合（セクション追加が必要）

軽微な閾値調整やパターン追加など要約が変わらない範囲なら更新不要。

配布方式自体（chezmoi自動インストール / marketplace経由など）を変えた場合は `docs/guide/claude-code.md` 側の修正も必要。
`README.md` 本体には各プラグイン固有の記述がないため、通常は修正不要。

## 手順

1. `plugins/<plugin-name>/.claude-plugin/plugin.json` の `version` (必要なら `description`) を更新
2. `.claude-plugin/marketplace.json` の該当プラグイン エントリを同一文字列に同期する
3. 必要なら `docs/guide/claude-code-guide.md` のチェック内容リストを更新
4. `make test` を実行し、SSOTテストを含む全テストがgreenであることを確認
5. 変更をコミット（通常の編集と同じコミットに含めてよい）

## 参考

- 配布方式と前提: `docs/guide/claude-code.md` のagent-toolkitセクション
- 利用者向け説明（チェック内容・更新手順）: `docs/guide/claude-code-guide.md`
- `agent-toolkit` の現行チェック内容: `plugins/agent-toolkit/scripts/pretooluse.py` モジュールdocstring
