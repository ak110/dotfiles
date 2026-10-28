# SSH設定管理（`update-ssh-config`）

`update-ssh-config`は、分割した設定ファイルから`~/.ssh/config`と`~/.ssh/authorized_keys`を生成するコマンドである。

## ファイル配置

```text
~/.ssh/
├── conf.d/
│   ├── 10_main.conf          # SSH config（chezmoi管理）
│   └── authorized_keys       # 公開鍵（chezmoi管理）
├── localconfig                # ローカル専用SSH config（任意、chezmoi管理外）
├── local_authorized_keys      # ローカル専用公開鍵（任意、chezmoi管理外）
├── config                     # 生成物（直接編集しない）
├── config.bk                  # 初回バックアップ
└── authorized_keys            # 生成物（手動追加分も保持される）
```

## 生成のしかた

- `~/.ssh/config`: `conf.d/*.conf`をファイル名順に結合し、`localconfig`があれば末尾に追加して毎回上書きする。初回だけ（`config.bk`が無い場合）既存のconfigを`config.bk`へ退避する。上書きされるため、configは直接編集せず`localconfig`を使う
- `~/.ssh/authorized_keys`: `conf.d/authorized_keys`と`local_authorized_keys`の鍵のうち、未登録のものだけを追加する。鍵はbase64部分で比べ、コメントの違いは無視する。既存の鍵（クラウドプロバイダや手動で追加した鍵を含む）は削除しない

鍵を削除する場合は、`~/.ssh/authorized_keys`を手動で編集して行を削除する。

## 実行

`chezmoi apply`で`conf.d/`内のファイルが変わると、`update-ssh-config`が自動で実行される。
`localconfig`や`local_authorized_keys`を変更した場合は、手動で実行する。

```bash
update-ssh-config
```
