---
paths:
  - "plugins/**"
  - ".claude-plugin/marketplace.json"
---

# Claude Code plugin 編集チェックリスト

本リポジトリ配下の`plugins/`と`.claude-plugin/marketplace.json`を編集するときに確認する。
バージョン更新・SSOT同期・ドキュメント同期は漏れが発生しやすいため、本ファイルで手順を確認する。

## 着手前チェック: 未プッシュコミットでbump済みか

プラグイン編集に着手する前、および`plan-mode`で計画ファイルを書き始める前に必ず実施する。
計画フェーズで確認し忘れるとbumpタスクが計画から抜け落ち、push直前に慌てる原因になる。

```bash
git log --decorate -p 'origin/master..HEAD' -- .claude-plugin/marketplace.json
```

- 差分があれば**bump済み**のため、今回の変更で再bumpは不要（後述の判定基準もスキップしてよい）
- 差分がなければ**未bump**のため、後述の判定基準に従って今回の変更でbumpする必要があるかを判断する

push前にはbumpが必須である。
同じバージョンでは`claude plugin update`が「最新です」と返すため、bumpしないと利用者へ配信されない
（過去に`agent-toolkit`（旧`edit-guardrails`）で実害があった）。

## SSOTの2ファイル

プラグインごとに以下の2箇所で`version`／`description`を完全に同一文字列に保つ。

- `plugins/<plugin-name>/.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`の`plugins[]`内`name == "<plugin-name>"`のエントリ

整合性は各プラグインのテストで検査する。
`agent-toolkit`の担当は`TestManifestSsot`で、`uv run pyfltr run`で自動的に失敗する
（場所: `plugins/agent-toolkit/tests/pretooluse_test.py`）。
新しいプラグインを追加するときは同等のSSOTテストも追加する。

## バージョン更新の判定基準

未bumpの場合、利用者に届く振る舞いが変わるものは必ずbumpする。

### bumpが必要な変更

- プラグインのhookスクリプトやentry pointのロジック変更
- 新しいcheck／機能の追加、既存checkの削除
- `hooks/hooks.json`など設定ファイルのmatcher／command変更
- 依存や実行環境要件の変更（`requires-python`／script headerのdependencies）
- ブロック条件の緩和（false positive対策でallowlistを増やす等）

### bumpが不要な変更

- コメント・docstringのみの修正
- `tests/`のみの追加・修正（SSOTテスト自身の変更を含む）
- 入出力が完全に不変なリファクタリング
- 誤字修正・スタイル調整

判断に迷う場合はbumpする方針とする（pre-1.0であれば頻繁にMINORを更新しても問題ない）。

### PATCH／MINOR／MAJORの使い分け

- PATCH（`+0.0.1`）: 軽微な修正（メッセージ変更、スタイル調整、バグ修正、検出漏れの修正など）
- MINOR（`+0.1.0`）: 機能追加、検出範囲の大幅拡大、descriptionが変わる規模の変更など、規模の大きい変更に限定
- MAJOR（`+1.0.0`）: ユーザーからの明示的な指示がない限り行わない

## 同期先ドキュメント

`docs/guide/claude-code-guide.md`の「agent-toolkit」セクションに各プラグインのチェック内容要約がある。
以下の変更をしたときはここも併せて更新する（更新忘れが起きやすいのでここに明記する）。

- 新しいcheckの追加・既存checkの削除
- 検出範囲の大きな変更（allowlist／blocklistの方針変更）
- 依存ツールの変更（`uv`以外を要求するようになった等）
- 新しいプラグインを追加した場合（セクション追加が必要）

軽微な閾値調整やパターン追加など要約が変わらない範囲なら更新不要。

配布方式自体（chezmoi自動インストール／marketplace経由など）を変えた場合は`docs/guide/claude-code.md`側の修正も必要。
`README.md`本体には各プラグイン固有の記述がないため、通常は修正不要。

## 手順

1. 冒頭の「着手前チェック」でbump要否を判定する
2. 必要に応じて`plugins/<plugin-name>/.claude-plugin/plugin.json`の`version`（および`description`）を更新
3. `.claude-plugin/marketplace.json`の該当プラグインエントリを同一文字列に同期する
4. 必要なら`docs/guide/claude-code-guide.md`のチェック内容リストを更新
5. `uv run pyfltr run-for-agent`を実行し、SSOTテストを含む全テストがgreenであることを確認
6. 変更をコミット（通常の編集と同じコミットに含めてよい）

## 計画・実装系スキルの連携

`plugins/agent-toolkit/skills/`配下の計画・実装系スキルとサブエージェントの対応を一覧する。
スキル本文や呼び出し対象のサブエージェントを編集する際、関連スキルでの参照箇所を更新し忘れないために用いる。
詳細な動作手順は各スキルのSKILL.mdが正であり、本節は概要のみを示す。

- `spec-driven`（任意）
  - 担当工程: 既存システムの大規模バージョンアップを想定した次版ドキュメント管理とワークフロー誘導
  - 連携サブエージェント: なし（計画・実装工程は`plan-mode`・`careful-impl`へ委譲）
- `plan-mode`
  - 担当工程: 計画ファイルの作成・codexレビュー
  - 連携サブエージェント: なし
- `careful-impl`
  - 担当工程: 計画合意後の実装・検証・レビュー・コミット（採用時のみ）
  - 連携サブエージェント: `careful-implementer`・`careful-spec-reviewer`・`careful-code-reviewer`・
    `careful-docs-reviewer`・`careful-followup-reviewer`

`spec-driven`が有効な場合は、同スキルの誘導に従って個別の作業テーマごとに`plan-mode`へ入り計画ファイルを作成する。
それ以外の場合は直接`plan-mode`から始める。

`plan-mode`が作成した計画ファイルは`ExitPlanMode`を合意ゲートとして通過する。
計画ファイル内に`careful-impl`の採用が明記されている場合のみ`careful-impl`へ引き継がれる
（不採用時はメインが計画ファイルの「検証手順」「コミット方針」に従って直接実装・検証・コミットを行う）。
引き継ぎ時にコンテキストが切れている前提で、計画ファイルが唯一の入力源として自立するよう漏れなく記述する。

```mermaid
flowchart TB
    SD["spec-driven スキル（任意）"]:::sd
    subgraph PM["plan-mode スキル"]
      P[計画ファイル作成<br/>codexレビュー]
    end
    subgraph PI["careful-impl スキル（採用時のみ）"]
      direction TB
      T[careful-implementer<br/>実装・検証] --> R1[careful-spec/code/docs-reviewer<br/>初回並列]
      R1 -->|指摘あり<br/>メインが統合| T2[careful-implementer<br/>修正再実装]
      T2 --> R2[careful-followup-reviewer<br/>haiku 単一]
      R2 -->|未対応あり| T2
      R1 -->|指摘なし| C[計画ファイルのコミット方針に従い<br/>メインがコミット]
      R2 -->|対応済み| C
    end
    SD -.->|作業テーマごとに誘導| PM
    PM -->|ExitPlanMode<br/>careful-impl 採用時のみ| PI

    classDef sd stroke-dasharray: 4 2
```

## 参考

- 配布方式と前提: `docs/guide/claude-code.md`のagent-toolkitセクション
- 利用者向け説明（チェック内容・更新手順）: `docs/guide/claude-code-guide.md`
- `agent-toolkit`の現行チェック内容: `plugins/agent-toolkit/scripts/pretooluse.py`モジュールdocstring
