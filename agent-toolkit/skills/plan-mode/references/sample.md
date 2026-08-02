# 計画ファイル サンプル例

````markdown
# ファイルアップロード上限を10MBから50MBへ引き上げ

## 変更履歴

- 初版: サーバー設定を正本として全経路の上限を50MBへ統一する
- レビュー指摘（第1ラウンド・指摘1）: プロキシ側上限の変更漏れ。採用: 変更対象と検証へ追加した。同期先: `### 実施内容`、`### 対象ファイル一覧`、`## 実行方法`
- レビュー指摘（第1ラウンド・指摘2）: ファイル種別別の上限追加。不採用: 利用者要件に無く運用が複雑化するため。同期先: `### 却下した代替案`
- ユーザー指摘（1件目）: 超過時の既存エラー文面を維持する。採用: 合意事項とテスト条件へ追加した。同期先: `### ユーザー合意済み事項`、`` ### `tests/upload_test.py` ``

## 背景

### 計画メタ情報

- 起動経路: plan-and-add-feedback経由
- 対象リポジトリ: `~/dotfiles`
- 作業種別: 通常変更
- ベースコミット: `a1b2c3d4`（`git rev-parse HEAD`実測。並行稼働セッションが対象ファイルへ
  コミットしうる場合はその旨と実装着手時の再確認手順への参照を付記する）

### 経緯

高解像度画像のアップロード要望が継続提示されており、ストレージと帯域の増強完了で上限引き上げが可能になった。

## 対応方針

### 実施内容

| 実施内容 | ユーザー指示との関係 | 根拠 |
| --- | --- | --- |
| アップロード上限をサーバー、クライアント、プロキシの全経路で50MBへ統一する | 具体化 | 利用者指定の50MBを、上限が適用される全経路へ反映するため |
| クライアント側の独立設定を廃止し、サーバー設定を参照する構成へ変更する | エージェント追加 | 上限値の再不整合を防ぎ、設定値の正本を一元化するため |
| 50MBの境界テストと設定値管理の文書を更新する | エージェント追加 | 変更後の公開契約を検証し、設定経路を利用者へ示すため |

### ユーザー合意済み事項

- 超過時のエラーメッセージは既存文面を維持する

### エージェント判断

なし

### 却下した代替案

- ファイル種別ごとに上限を変える案。運用が複雑化する割に必要性が明確でないため

### 恒久化・リファクタリング内容

#### 恒久化

なし

#### リファクタリング

クライアント側の独立保持を解消し設定API経由のSSOTへ統一する（本計画に含める）。

#### 類似見直し

母集団は「上限値を判断規則として直書きする箇所」とし、点検観点は当該値の変更時に追随が必要かとする。

母集団抽出に用いた実測コマンドと出力は次のとおりである。

```text
$ grep -rnE 'MAX_UPLOAD_BYTES|client_max_body_size' server/ client/ infra/
server/config.py:12:MAX_UPLOAD_BYTES = 10 * 1024 * 1024
client/limits.ts:3:export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
infra/nginx.conf:8:client_max_body_size 20M;
infra/nginx.conf:31:client_max_body_size 1M;
```

出力4行のうち先頭3行は対象ファイル一覧の`server/config.py`・`client/limits.ts`・`infra/nginx.conf`へ対応する。
4行目はアバター画像専用の`location`ブロックの上限であり、アップロード経路の上限とは独立に定めた値のため非該当とする。
他に残存なし。

## 調査結果

### 対象ファイルの現状

- `server/config.py`: 現行40行
- `client/limits.ts`: 現行18行
- `infra/nginx.conf`: 現行62行
- `tests/upload_test.py`: 現行85行
- `docs/architecture/limits.md`: 現行30行

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

差分・参照箇所の特定に行番号を用いず、H2・H3見出し名または対象ファイル中の一意な既存文字列を
アンカーとして用いる（`server/config.py`のファイル別H3配下の記載例を参照）。

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

変更前:

```text
該当節なし
```

変更後:

```text
## 設定値管理

- サーバー`server/config.py`の`MAX_UPLOAD_BYTES`をSSOTとする
- クライアント`client/limits.ts`は設定API経由でサーバー値を取得する
- プロキシ`infra/nginx.conf`の`client_max_body_size`をサーバー値と一致させる
```

## 実行方法

0. 並行変更の確認と保存（対象ファイルが並行稼働セッションの変更の影響を受ける可能性がある場合）：
   - `git rev-parse HEAD`と`### 計画メタ情報`のベースコミットを比較し、差分があれば
     アンカー該当箇所を再確認する
- （呼び出し元が実施）Agentツールで`agent-toolkit:plan-impl-executor`を起動する
  - `agent-toolkit:coding-standards`を呼び出す
- `agent-toolkit:commit`スキルを呼び出す
- 想定コミット単位ごとに実装、近接検証、差分確認、コミットを反復する
  - アップロード上限の実装とテスト
    - 対象: `server/config.py`・`client/limits.ts`・`infra/nginx.conf`・`tests/upload_test.py`
    - 近接検証: `uvx pyfltr run tests/upload_test.py`・`tsc --noEmit client/limits.ts`・
      `nginx -t -c infra/nginx.conf`
    - 件名案: `feat(upload): ファイルサイズ上限を50MBへ引き上げる`
  - 設定値管理の文書
    - 対象: `docs/architecture/limits.md`
    - 近接検証: `uvx pyfltr run docs/architecture/limits.md`
    - 件名案: `docs(upload): アップロード上限の設定値管理を文書化する`
- 実装差分に応じてコミット境界を変更した場合は、計画本文と`## 進捗ログ`を同期する
- 全コミット完了後の最終検証: `uvx pyfltr run tests/upload_test.py docs/architecture/limits.md`・
  `tsc --noEmit client/limits.ts`・`nginx -t -c infra/nginx.conf`
- `plan-impl-executor`が計画準拠系と独立系の実装差分レビューを並列実行する

## 進捗ログ

## 計画ファイル（本ファイル）のパス

`~/.claude/plans/upload-limit-increase-a1b2.md`
````
