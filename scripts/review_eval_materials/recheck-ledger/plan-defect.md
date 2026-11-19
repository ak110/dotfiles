# 再レビューで前回の各指摘へ判定を付け、収束を判定から決める

## 概要

レビュー担当が前回の各指摘へ判定を付ける`recheck`を加え、`summary`の収束をレビュー担当の判定から決める。

### 計画メタ情報

- 起動経路: `agent-toolkit:process-wi`
- 対象リポジトリ: `/path/to/worktree`
- 関連WI:
  - 20000101-000000-003.md: 再レビューで前回の各指摘へ判定を付け、収束を判定から決める
- 作業種別: 通常変更

## 実施内容

1. 行へ判定の記録（`checks`）と区分（`category`）を足し、`recheck`で判定を追記する。
2. `summary`へ、判定の無い件数、判定の件数、収束の有無を足す。
3. 2以上のroundの指摘に区分を求め、一括入力の案内を改める。

## 要件・外部仕様

### 変更対象と直接影響範囲

変更対象は`ledger/store.py`、`ledger/cli.py`、`tests/store_test.py`、`tests/cli_test.py`である。
行の形式に`checks`と`category`を足すため、行を作る`add`・`add-many`と、行を読む`summary`を合わせて直す。

## 設計・実装方針

- 判定は行の`checks`へ追記し、以前の判定を消さない。
  集計は、各指摘についてそのround以前で最も新しい判定を使う。
- 現在のroundは記録済みの最大のroundとし、`recheck`はそれより前へ記録しない。
- 対応不十分・未対応の判定は、今回のroundに「前回から残存」として登録した行を`--residual`で指す。

## 検証

| 区分 | 検証コマンド |
| --- | --- |
| 変更範囲の検証 | `python3 -m unittest -v tests.store_test tests.cli_test` |

## 公開工程

統合準備完了を返し、統合の許可を待つ。

## 進捗ログ

| 順 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
| 1 | 実装commit | 判定の記録、集計、区分、案内を1つのcommitにした。 |
| 2 | 変更範囲の検証 | `python3 -m unittest -v tests.store_test tests.cli_test`で9件成功した。 |

## 評価用の対象

評価用の合成素材である。
進捗ログの結果のほかに、保存した実行の証拠は無い。
