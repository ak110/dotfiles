# 運用機能: chezmoiの命名規則

## chezmoiの命名規則（早見表）

`.chezmoi-source/`配下のファイル名は以下の代表規則で`~/`配下にデプロイされる。
詳細は<https://www.chezmoi.io/reference/source-state-attributes/>を参照する。

- `dot_<name>` → `~/.<name>`
- `private_<name>` → パーミッション`600`／ディレクトリは`700`
- `executable_<name>` → 実行権限付きで配置
- `exact_<name>`（ディレクトリ） → 配布元に無い項目を`chezmoi apply`時に配布先から削除する。
  `make update-mise-locks`はmiseのロック用ディレクトリ（`dot_config/mise/dot_mise/locks/exact_<ツール>`）へこの属性を付け、版を更新しても旧版が配布先に残らないようにする
- `<name>.tmpl` → Goテンプレートとして評価
- `run_onchange_after_<name>.sh.tmpl` → `chezmoi apply`時の変更検知実行
- よく使うコマンド: `chezmoi apply`（反映）・`chezmoi diff`（差分確認）・`chezmoi managed`（配布対象確認）
