# 計画ファイル サンプル例

````markdown
# ファイルアップロード上限を10MBから50MBへ引き上げ

## 変更履歴

- 初版

## 背景

### 計画メタ情報

- 起動経路: plan-and-add-feedback経由
- 対象リポジトリ: `~/dotfiles`

### 経緯

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

#### 恒久化

なし

#### リファクタリング

クライアント側の独立保持を解消し設定API経由のSSOTへ統一する（本計画に含める）。

#### 類似見直し

上限値を直書きする箇所は`server/config.py`・`client/limits.ts`・`infra/nginx.conf`の3箇所のみで、他に残存なし。

## 調査結果

### 対象ファイルの現状

- `server/config.py`: 現行40行
- `client/limits.ts`: 現行18行
- `infra/nginx.conf`: 現行62行
- `tests/upload_test.py`: 現行85行

### 事実確認済み事項

- `server/config.py`サーバー側上限を10MBとして定義（Read確認）
- `client/limits.ts`クライアント側で同値を独立保持し整合機構なし（Read確認）
- `infra/nginx.conf`リバースプロキシ`client_max_body_size`が20MB（Read確認）
- `tests/upload_test.py`の上限境界テストはいずれも10MB前提（Read確認）

## 変更内容

### 対象ファイル一覧

- [ ] `server/config.py`（現行40行）
- [ ] `client/limits.ts`（現行18行）
- [ ] `infra/nginx.conf`（現行62行）
- [ ] `tests/upload_test.py`（現行85行）
- [ ] `docs/architecture/limits.md`（現行30行）

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
- 検証: `uvx pyfltr run-for-agent <対象テストファイル>`（対象はupload_testモジュール）
- `agent-toolkit:commit`スキルを呼び出す
- コミットする
  - 件名案: `feat(upload): ファイルサイズ上限を50MBへ引き上げる`
- `agent-toolkit:careful-review`スキルを呼び出す

## 進捗ログ

## 計画ファイル（本ファイル）のパス

`~/.claude/plans/upload-limit-increase-concurrent-hickey.md`
````
