---
name: pytilpack-usage
description: >
  pytilpackのモジュール構成・API・extras・CLIの使い方を参照するときに起動する。
  `import pytilpack`を含むコードを読み書きする時に起動する。
# 編集時の注意点: pytilpack本体の使用方法を変更した場合は本ファイルの更新漏れに注意する。
---

# pytilpackの使い方

本スキルは、pytilpackの構成とAPIに関する知識を提供する。
pytilpackはPythonのユーティリティ集で、各種ライブラリ向けの拡張とフレームワーク非依存の汎用モジュールを提供する。

## 基本的なimport方式

既存プロジェクトで利用する場合:

```python
import pytilpack.xxx
```

`xxx`には対象ライブラリ名（`httpx`、`pathlib`等）または汎用モジュール名（`cache`、`sse`等）が入る。
ライブラリ用モジュール（特にBabel・SQLAlchemyなど依存サイズが大きいライブラリ）はextrasのインストールが必要。

新規プロジェクトへの追加は`uv add`を使う:

```bash
uv add pytilpack                      # コア機能のみ
uv add "pytilpack[babel,sqlalchemy]"  # extras指定
```

## モジュール分類

モジュールは、追加依存なしまたは軽量依存で利用できる標準・軽量依存モジュールと、
対象ライブラリの拡張を提供しextras指定を要するライブラリ用ユーティリティに分かれる。
extrasが必要なモジュールは`pyproject.toml`の依存指定時にextras名を含めて指定する。

収録モジュールの一覧は後述のllms.txtで確認する（版により増減するため本ファイルへ列挙しない）。

## CLIツール

`pytilpack`コマンドで以下のサブコマンドを利用できる。

- `babel`: i18nメッセージ管理（`[babel]`必要）
- `delete-empty-dirs`: 空ディレクトリの削除
- `delete-old-files`: N日以上経過したファイルの削除
- `fetch`: Webコンテンツの取得
- `mcp`: MCPサーバーの起動
- `sync`: ディレクトリの一方向同期
- `wait-for-db-connection`: DB接続待機（`[sqlalchemy]`必要）

## APIドキュメントの参照方法

pytilpackのAPIの詳細情報が必要な場合は、llms.txtをWebFetchで取得する。
llms.txtはモジュール別のリンク集であり、必要なモジュールのページだけ個別に取得する。

<https://ak110.github.io/pytilpack/llms.txt>

### 段階的な取得手順

1. まず上記のllms.txtを取得してモジュール一覧と各ページのURLを確認する
2. 必要なモジュールのURLだけを個別に取得する（例: `https://ak110.github.io/pytilpack/api/functools/index.md`）
