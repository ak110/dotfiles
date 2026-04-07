---
paths:
  - "plugins/**"
  - ".claude-plugin/marketplace.json"
---

# Claude Code plugin 編集チェックリスト

本リポジトリ配下の `plugins/` と `.claude-plugin/marketplace.json` を編集するときに確認する。
バージョン更新・SSOT 同期・ドキュメント同期が別の場所に散らばっていて漏れやすいため、
ここに集約している。

## なぜ必要か

プラグインは `claude plugin update` 経由で配布される。
バージョン番号を上げない限り既存ユーザーへ更新が降りてこない。
(同じバージョンでは `update` コマンドが「最新です」と返す。)
過去に `edit-guardrails` で実害があったため、編集のたびに bump 要否を判定する。

また、バージョン情報は SSOT 違反の状態で 2 ファイルに重複している。
片方だけ更新すると配布が壊れるので両方揃える。

## SSOT の 2 ファイル

プラグインごとに以下の 2 箇所で `version` / `description` を完全に同一文字列に保つ。

- `plugins/<plugin-name>/.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json` の `plugins[]` 内 `name == "<plugin-name>"` のエントリ

整合性は各プラグインのテストで検査する。
`edit-guardrails` の担当は `TestManifestSsot` で、`make test` で自動的に落ちる。
(場所: `plugins/edit-guardrails/tests/pretooluse_test.py`)
新しいプラグインを追加するときは同等の SSOT テストも追加する。

## bump が必要な変更

ユーザーに届く振る舞いが変わるもの。以下のいずれかに該当したら必ず bump する。

- プラグインの hook スクリプトや entry point のロジック変更
- 新しい check / 機能の追加、既存 check の削除
- `hooks/hooks.json` など設定ファイルの matcher / command 変更
- 依存や実行環境要件の変更 (`requires-python` / script header の dependencies)
- ブロック条件の緩和 (false positive 対策で allowlist を増やす等)

## bump が不要な変更

- コメント・docstring のみの修正
- `tests/` のみの追加・修正 (SSOT テスト自身の変更を含む)
- 入出力が完全に不変なリファクタリング
- 誤字修正・スタイル調整

判断に迷ったら bump する方向へ倒す (pre-1.0 なら頻繁に MINOR が上がっても構わない)。

## bump 指針 (pre-1.0 `0.x.y`)

- 機能追加 / 検出範囲拡大 / description が変わるレベルの変更 → MINOR (`x` を +1、`y` を 0 にリセット)
- 既存挙動のバグ修正 / 検出漏れ修正 → PATCH (`y` を +1)

破壊的変更の概念は 1.0 までは気にしない。

## 同期先ドキュメント

`docs/claude-code.md` の「Claude Code plugin (...)」セクションに各プラグインのチェック内容要約がある。
以下の変更をしたときはここも併せて更新する (更新忘れが起きやすいのでここに明記する)。

- 新しい check の追加・既存 check の削除
- 検出範囲の大きな変更 (allowlist / blocklist の方針変更)
- 依存ツールの変更 (`uv` 以外を要求するようになった等)
- 新しいプラグインを追加した場合 (セクション追加が必要)

軽微な閾値調整やパターン追加など要約が変わらない範囲なら更新不要。

`README.md` 本体には各プラグイン固有の記述はないので通常は触らなくてよい。

## 手順

1. `plugins/<plugin-name>/.claude-plugin/plugin.json` の `version` (必要なら `description`) を更新
2. `.claude-plugin/marketplace.json` の該当プラグイン エントリを同一文字列に揃える
3. 必要なら `docs/claude-code.md` のチェック内容リストを更新
4. `make test` を実行し、SSOT テストを含む全テストが green であることを確認
5. 変更をコミット (通常の編集と同じコミットに含めてよい)

## 参考

- 配布方式と前提: `docs/claude-code.md` の edit-guardrails セクション
- `edit-guardrails` の現行チェック内容: `plugins/edit-guardrails/scripts/pretooluse.py` モジュール docstring
