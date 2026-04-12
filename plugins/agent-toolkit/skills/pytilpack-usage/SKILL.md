---
name: pytilpack-usage
description: pytilpackの使い方・モジュール構成・APIドキュメント参照のリファレンス。pytilpackのAPIや関数の使い方を調べたいとき、pytilpackのモジュールを新たに使い始めるとき、pytilpackの依存（extras）を追加・確認したいとき、pytilpackのCLIツールを使いたいときに使う。`pytilpack`が`pyproject.toml`や`CLAUDE.md`に記載されているプロジェクトで特に有用。`import pytilpack`を含むコードを読み書きする際にも参照する。
user-invocable: true
---

# pytilpackの使い方

pytilpackはPythonのユーティリティ集で、各種ライブラリ向けの拡張とフレームワーク非依存の汎用モジュールを提供する。

## 基本的なimport方式

```python
import pytilpack.xxx
```

`xxx`には対象ライブラリ名（`httpx`、`pathlib`等）または汎用モジュール名（`cache`、`sse`等）が入る。

## モジュール分類

### ライブラリ用ユーティリティ

対象ライブラリの拡張機能を提供する。対応するextrasのインストールが必要な場合がある。

```text
asyncio, babel, base64, csv, dataclasses, datetime, fastapi, flask,
flask_login, fnctl, functools, httpx, importlib, json, logging, markdown,
msal, pathlib, pycrypto, pydantic, pytest, python, quart, quart_auth,
sqlalchemy, threading, threadinga, tiktoken, tqdm, typing, yaml
```

### 汎用モジュール

フレームワーク非依存のユーティリティ。追加依存なしで利用できるものが多い。

```text
cache, crypto, data_url, environ, healthcheck, htmlrag, http, i18n,
io, jsonc, paginator, random, ratelimit, secrets, sse, validator, web
```

## CLIツール

`pytilpack`コマンドで以下のサブコマンドを利用できる。

- `babel` — i18nメッセージ管理（`[babel]`必要）
- `delete-empty-dirs` — 空ディレクトリの削除
- `delete-old-files` — N日以上経過したファイルの削除
- `fetch` — Webコンテンツの取得
- `mcp` — MCPサーバーの起動
- `sync` — ディレクトリの一方向同期
- `wait-for-db-connection` — DB接続待機（`[sqlalchemy]`必要）

## APIドキュメントの参照方法

pytilpackのAPIの詳細情報が必要な場合は、llms.txtをWebFetchで取得する。
llms.txtはモジュール別のリンク集であり、必要なモジュールのページだけ個別に取得する。

<https://ak110.github.io/pytilpack/llms.txt>

### 段階的な取得手順

1. まず上記のllms.txtを取得してモジュール一覧と各ページのURLを確認する
2. 必要なモジュールのURLだけを個別に取得する（例: `https://ak110.github.io/pytilpack/api/functools/index.md`）

llms-full.txt（全API文書）は非常に大容量のため一括取得は避ける。
