# 計画ファイル サンプル例

````markdown
# ファイルアップロード上限を10MBから50MBへ引き上げ

## 変更履歴

- 初版

## 背景

高解像度画像のアップロード要望が継続提示されており、ストレージと帯域の増強完了で上限引き上げが可能になった。

## 対応方針

### ユーザー合意済み事項

- 上限値は50MBに変更
- 超過時エラーメッセージは既存流用

### エージェント判断

- 上限値定義の一元化でSSOTを確立。`docs/architecture/limits.md`「設定値管理」節に3層整合ルールを追記

### 却下した代替案

- ファイル種別ごとに上限を変える案。運用が複雑化する割に必要性が明確でないため

### 恒久化・リファクタリング内容

- 恒久化: 3層整合運用方針を`docs/architecture/limits.md`へ追記。リファクタリング: クライアント側独立保持を解消し設定API経由のSSOTへ統一

## 調査結果

- `server/config.py:34`サーバー側上限を10MBとして定義。`client/limits.ts:12`クライアント側で同値を独立保持し整合機構なし
- `infra/nginx.conf:58`リバースプロキシ`client_max_body_size`が20MB。`tests/upload_test.py:40-78`上限境界テストはいずれも10MB前提

## 変更内容

### 対象ファイル一覧

- [ ] `server/config.py`
- [ ] `client/limits.ts`
- [ ] `infra/nginx.conf`
- [ ] `tests/upload_test.py`
- [ ] `docs/architecture/limits.md`

### `server/config.py`

```text
-MAX_UPLOAD_BYTES = 10 * 1024 * 1024
+MAX_UPLOAD_BYTES = 50 * 1024 * 1024
```

### `infra/nginx.conf`

```text
-client_max_body_size 20M;
+client_max_body_size 50M;
```

### `client/limits.ts`

独立保持を解消し設定API経由でサーバーから取得する形へ統一する。

```text
-export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
+export const MAX_UPLOAD_BYTES = (await fetchLimits()).maxUploadBytes;
```

### `tests/upload_test.py`

新上限基準で改訂し、同値分割・境界値分析でケースを抽出する。

```text
-assert upload(make_blob(10 * 1024 * 1024)).status == 200
+assert upload(make_blob(50 * 1024 * 1024)).status == 200
+assert upload(make_blob(50 * 1024 * 1024 + 1)).status == 413
+assert upload(make_blob(0)).status == 400
```

### `docs/architecture/limits.md`

「設定値管理」節に3層整合ルールを追記する。

```text
+## 設定値管理
+
+- サーバー`server/config.py`の`MAX_UPLOAD_BYTES`をSSOTとする
+- クライアント`client/limits.ts`は設定API経由でサーバー値を取得する
+- プロキシ`infra/nginx.conf`の`client_max_body_size`をサーバー値と一致させる
```

## 実行方法

- Agentツールで`agent-toolkit:plan-impl-executor`を起動する
  - `agent-toolkit:coding-standards`を呼び出す
- 計画に従い実装する
- 検証: `uvx pyfltr run-for-agent tests/upload_test.py`
- `agent-toolkit:commit`スキルを呼び出す
- コミットする
  - 件名案: `feat(upload): ファイルサイズ上限を50MBへ引き上げる`
- `agent-toolkit:careful-review`スキルを呼び出す

## 進捗ログ

## 計画ファイル（本ファイル）のパス

`~/.claude/plans/upload-limit-increase-concurrent-hickey.md`
````

`agent-toolkit:agent-standards`「文書サイズ上限」節の対象ファイルを含む計画では、
`## 調査結果`直下に「### 対象ファイルの現状」H3節を配置し、対象ファイル全件の現行`wc -l`実測値を列挙する。

## メタ規範新設計画のサンプル

規範改訂計画（メタ規範新設パターン: 新規節見出し追加・全称禁止バレット・汎用禁止形バレット）のサンプル。
必須項目の内訳は`agent-toolkit/skills/plan-mode/references/norm-revision-checklist.md`
「規範対象範囲の網羅確認」節の規定に従う。

````markdown
# サブエージェント委譲時の縮退表明抑止規範追加

## 変更履歴

- 初版

## 背景

サブエージェント委譲時に縮退禁止規定を明示引用する運用が抜けており、
サブエージェント側で縮退表明が発生する事象が観測された。

## 対応方針

`agent-toolkit/rules/example-rule.md`「セッション分割・別計画化は禁止する」節へ全称禁止形バレットを追加し、
サブエージェント委譲経路でも縮退表明が抑止される規範に拡張する。

### ユーザー合意済み事項

- 上記節への全称禁止形バレット追加を採用する

### エージェント判断

- 追加は既存節末尾へバレット1件で反映する
- 版更新（bump種別の事前検査）: 以下のマトリクスに従いPATCH bumpを選ぶ。
  MINOR判定基準（description変更・トリガーキーワード追加・節新設・
  単一ファイル内で複数節に跨る規範改訂）への該当は無し

| ファイル | 改訂節数 | 節名 | 判定 | 該当基準 |
| --- | --- | --- | --- | --- |
| `agent-toolkit/rules/example-rule.md` | 1 | `セッション分割・別計画化は禁止する` | PATCH寄与 | 単一節改訂 |

### 却下した代替案

- なし

### 恒久化・リファクタリング内容

- なし

## 調査結果

### 対象ファイルの現状

- `agent-toolkit/rules/example-rule.md`: 現行180行（`wc -l`実測値）

### 遡及スキャン結果

- 対象パターン: 全称禁止形バレット（「いかなる理由（例: X）があってもYしない」形式）
- 検出件数と対応方針: 既存3件（すべてexample-rule.md内、追加バレットとの重複0件）で新規事象を扱う

## 変更内容

### 対象ファイル一覧

- [ ] `agent-toolkit/rules/example-rule.md`

### `agent-toolkit/rules/example-rule.md`

「セッション分割・別計画化は禁止する」節末尾へ全称禁止形バレット1件を追加する。

追記内容:

```text
- サブエージェント委譲経路でも本節の縮退禁止規定を適用する。
  いかなる理由（例: 委譲元スキルの完遂目標到達優先・多段ネスト起動での効率化）があっても、
  委譲先で縮退表明を発行しない
```

## 実行方法

- Agentツールで`agent-toolkit:plan-impl-executor`を起動する
- `agent-toolkit-edit`
- 実装
- 検証: `uvx pyfltr run-for-agent`
- `agent-toolkit:commit`の規範に従いコミットする
  - 件名案: `feat(agent-toolkit): サブエージェント委譲時の縮退表明抑止規範追加`
- `agent-toolkit:careful-review`
- `git push`
- push後CI通過確認

## 進捗ログ

## 計画ファイル（本ファイル）のパス

`~/.claude/plans/subagent-degradation-suppression-sample.md`
````
