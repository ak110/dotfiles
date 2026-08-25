# 複数リポジトリへの投入

- 原則としてリポジトリごとに単独で完結するフィードバックへ分け、各リポジトリの現行状態を個別に事前確認する
- 実装順序が必要な場合は先行のファイル名を後続のフィードバックの`depends_on`へ記録する
- 同一変更が複数のリポジトリで不可分であり、単一計画・単一検証でしか成立しない場合だけ1つの計画へ集約する
- 投入元固有の後続本文は各フィードバック本文へ直接記し、処理側の将来ステップによる自動生成を前提にしない
- 別リポジトリへ移管する項目は、元項目のfrontmatterと本文を含むメッセージ全体を正しい`target_repo`へ移管して`agent-toolkit:add-feedback`で登録する
- 投入前処理で入力メッセージの予約frontmatterキー`target_repo`だけを移管先の値へ一時的に置き換える
- 通常の`atk mq add`はfrontmatterの`target_repo`をCLI値で置き換えず、frontmatterの値を優先する
- `alert_keys`などの非予約frontmatterは元項目の値を保持する
- source欄がない場合はsourceを指定しない。指定済みsourceがある場合は同じ値を渡す
- 登録後は`atk mq show <移管先ファイル名> --target-repo=<target_repo> --skip-pull`で、移管先ファイル名、`target_repo`、本文、指定済みsource及び元項目の非予約frontmatter全体を照合する
- 登録と照合の成功後だけ、元項目を移管先リポジトリとファイル名付きの項目固有メモでrejectする
