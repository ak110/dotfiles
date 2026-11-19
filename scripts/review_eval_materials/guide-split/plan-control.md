# 文章の基準を用途別の資料へ分ける

## 概要

`guides/writing.md`の用途別の基準を別の資料へ分け、資料の登録と使い方の説明を分けた後の構成にそろえる。

### 計画メタ情報

- 起動経路: `agent-toolkit:atk-mode`の作業process-wi
- 対象リポジトリ: `/path/to/worktree`
- 関連WI:
  - 20000101-000000-001.md: 文章の基準を用途別の資料へ分ける
- 作業種別: 通常変更

## 実施内容

1. 業務文書と調査報告の節を、用途別の資料へ移す。
2. `GUIDES`へ分けた資料を登録し、資料の試験を分けた後の見出しへ直す。
3. 使い方の説明の参照を、分けた後の資料へ付け替える。

## 要件・外部仕様

### 変更対象と直接影響範囲

変更対象は`guides/writing.md`、`guides/writing-business.md`（新規）、`guides/writing-research.md`（新規）、`guidekit/catalog.py`、`docs/usage.md`、`tests/catalog_test.py`である。
固定文字列`guides/`、`GUIDES`、`調査報告`の探索で、変更対象のほかに次の利用側が一致した。

- `guidekit/cli.py:26`（`list`が`GUIDES`の全ての資料を出す）
- `tests/usage_links_test.py:6`（説明の参照が実在の見出しを指すかを確かめる）
- `tests/cli_list_test.py:14`（`list`の出力が`GUIDES`の全ての資料を覆うかを確かめる）

### 受入シナリオ

| シナリオ | 操作 | 期待結果 |
| --- | --- | --- |
| S1 見出しの検索 | `python3 -m guidekit.cli find 調査報告` | `writing-research`を出す。 |
| S2 一覧 | `python3 -m guidekit.cli list` | 3つの資料と各資料の見出しを出す。 |

## 検証

| 区分 | 検証コマンド |
| --- | --- |
| 変更範囲の検証 | `python3 -m unittest -v tests.catalog_test tests.usage_links_test tests.cli_list_test` |

## 公開工程

統合準備完了を返し、統合の許可を待つ。

## 進捗ログ

| 順 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
| 1 | 実装commit | 資料の分割、登録、説明の付け替えを1つのcommitにした。 |
| 2 | 変更範囲の検証 | 検証表の3対象を実行し、5件成功した。 |
| 3 | 個別の結果 | `tests.catalog_test`3件、`tests.usage_links_test`1件、`tests.cli_list_test`1件がそれぞれ成功した。 |
| 4 | 受入シナリオ | S1は`writing-research`、S2は3つの資料と各見出しを出した。 |

## 評価用の対象

評価用の合成素材である。
進捗ログの結果のほかに、保存した実行の証拠は無い。
