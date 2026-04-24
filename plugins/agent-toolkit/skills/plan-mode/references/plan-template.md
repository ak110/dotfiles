# 計画ファイルテンプレート

計画ファイル作成時に参照する記述例集。
単一フェーズ計画の全セクション記述例（`careful-impl`不採用パターン）と、
複数フェーズ計画の骨子例（`careful-impl`採用パターン）を収録する。

## 全セクションを通した記述例

以下は`careful-impl`不採用（既定）の単一フェーズ計画の記述例。
`## 実装・検証・レビュー`節は「検証手順」「コミット方針」の2項目のみを記述する。

```markdown
# ファイルアップロード上限を10MBから50MBへ引き上げ

## 背景

高解像度画像をアップロードできないという要望が継続して寄せられている。
ストレージと転送帯域の増強が完了し運用コストの懸念が解消されたため、上限値を引き上げて要望に応える。

## 対応方針

### ユーザー合意済み事項

- 上限値は50MBに変更
- 超過時のエラーメッセージ文言は既存のものを流用
- 経路上の各層に個別設定されているサイズ制限やタイムアウトを事前に確認し、整合確認を検証範囲に含める

### エージェント判断

- 上限値の定義を一元化してSSOTを確立
- 既存の超過判定とエラー通知の挙動は維持し、参照値のみを差し替え
- `careful-impl`は不採用とする（軽微な設定値変更で判断の余地が少ないため）

### 却下した代替案

- ファイル種別ごとに上限を変える案 — 運用が複雑化する割に現時点で必要性が明確でないため
- 上限を撤廃する案 — 想定外のリソース消費や不正利用のリスクが残るため

## 調査結果

- サーバー側の上限定義: `server/config.py:34`
- クライアント側の上限定義: `client/limits.ts:12`（サーバーと独立して保持されており不整合が発生しやすい）
- リバースプロキシのbody size上限: `infra/nginx.conf:58` で20MB
- 関連テスト: `tests/upload_test.py:40-78`

## 変更内容

- 修正: `server/config.py:34` の上限値を50MBへ変更
- 削除: `client/limits.ts:12` の重複定義を撤去し、設定API経由で取得する形へ変更
- 修正: `infra/nginx.conf:58` のbody size上限を50MBへ引き上げ
- 修正: `tests/upload_test.py:40-78` の境界値ケースを更新

## 実装・検証・レビュー

- 検証手順
  - `uv run pyfltr run-for-agent tests/upload_test.py`
  - 追加したテストが修正前は失敗し修正後は成功することを確認する
- コミット方針: コミットする。subject: `feat(upload): ファイルサイズ上限を50MBへ引き上げる`

## 変更履歴

- 初版
- codexレビュー反映
  - 指摘: リバースプロキシのbody size上限が未考慮 → 対応: 調査結果と変更内容に反映
  - 指摘: 新旧の上限値を切り替えるfeature flagを用意すべき → 不対応: 切り戻しは設定値の差し替えで十分対応でき、feature flagを追加する運用複雑度の増加に見合う利点がないため
- ユーザー指示反映
  - 検証手順を `uv run pyfltr run-for-agent tests/upload_test.py` の実行に変更

## 計画ファイル

`~/.claude/plans/upload-limit-increase-concurrent-hickey.md`
```

## 複数フェーズ計画の骨子例

以下は`careful-impl`採用パターンの骨子例。
`## 実装・検証・レビュー`節に「実装時に事前呼び出しが必要なスキル」「検証手順」「レビュー実施方針」「コミット方針」の
4項目を記述する。
`## 変更内容`と`## 実装・検証・レビュー`のみ抜粋する。

```markdown
## 変更内容

### フェーズ1: 既存テスト整備

- 修正: `tests/upload_test.py` のフィクスチャを共通化

### フェーズ2: 上限値の引き上げ

- 修正: `server/config.py` の上限定義を50MBへ
- 修正: `infra/nginx.conf` のbody size上限を50MBへ

## 実装・検証・レビュー

- 実装時に事前呼び出しが必要なスキル
  - `agent-toolkit:careful-impl`
  - `agent-toolkit:coding-standards`

### フェーズ1: 既存テスト整備

- 検証手順
  - `uv run pyfltr run-for-agent`
- レビュー実施方針: `careful-code-reviewer`のみ起動。仕様変更を伴わないため`careful-spec-reviewer`は省略
- コミット方針: コミットする。subject: `test(upload): フィクスチャを共通化する`

### フェーズ2: 上限値の引き上げ

- 検証手順
  - `uv run pyfltr run-for-agent`
  - 50MB境界のテストが追加されており修正前は失敗・修正後は成功することを確認する
- レビュー実施方針: 既定通り`careful-spec-reviewer`・`careful-code-reviewer`・`careful-docs-reviewer`の
  3エージェントをすべて並列起動する
- コミット方針: コミットする。subject: `feat(upload): ファイルサイズ上限を50MBへ引き上げる`
```
