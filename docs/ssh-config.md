# SSH設定管理 (`update-ssh-config`)

`update-ssh-config` はSSH configとauthorized_keysを分割ファイルから生成するコマンド。

## 概要

- `~/.ssh/config`
  - 入力: `conf.d/*.conf` + `localconfig`
  - 動作: 毎回上書き生成（初回バックアップあり）
- `~/.ssh/authorized_keys`
  - 入力: `conf.d/authorized_keys` + `local_authorized_keys`
  - 動作: 既存にない鍵のみ追加（削除しない）

## ファイル配置

```text
~/.ssh/
├── conf.d/
│   ├── 10_main.conf          # SSH config（chezmoi管理）
│   └── authorized_keys       # 公開鍵（chezmoi管理）
├── config                     # 生成物（直接編集しない）
├── config.bk                  # 初回バックアップ
├── authorized_keys            # 生成物（手動追加分も保持される）
├── localconfig                # ローカル専用SSH config（任意、chezmoi管理外）
└── local_authorized_keys      # ローカル専用公開鍵（任意、chezmoi管理外）
```

## SSH config生成

`~/.ssh/conf.d/*.conf` をファイル名順に結合し、
`~/.ssh/localconfig` があれば末尾に追加して
`~/.ssh/config` を生成する。

- 初回実行時（`~/.ssh/config.bk` が存在しない場合）、既存のconfigをバックアップする
- 毎回上書きされるため、configの直接編集はlocalconfigを使うこと

## authorized_keys生成

`conf.d/authorized_keys` と `local_authorized_keys` の鍵を、
既存の `authorized_keys` に含まれていなければ追加する。

- 追加のみ: 既存の鍵は削除されない。クラウドプロバイダや手動で追加した鍵も保持される
- 重複判定: base64鍵データ部分で比較（コメントの違いは無視）
- 鍵の削除: `~/.ssh/authorized_keys` を手動で編集して行を削除する

## 自動実行

chezmoi apply時、`conf.d/` 内のファイルが変更されると
自動的に `update-ssh-config` が実行される
（`run_onchange` テンプレート）。

`localconfig` や `local_authorized_keys` はchezmoi管理外のため、
変更時は手動で `update-ssh-config` を実行すること。

## 手動実行

```bash
update-ssh-config
```
