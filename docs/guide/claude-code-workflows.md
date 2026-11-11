# agent-toolkitの作業パターン

## 推奨ワークフロー

作業の進め方は次の4パターンから選ぶ。

| パターン | 向く場面 |
| --- | --- |
| 対話型 | 要件をその場で確定しながら進めたい |
| 自律型 | AWIが継続して発生し、常駐実行で順に処理させたい |
| まとめ処理型 | 数日に数件の頻度でたまったAWIをまとめて処理したい |
| 定期実行型 | 決まったスキルを一定の間隔で自動実行したい |

どのパターンでも、エージェントは技術的に決められる事項を自分で判断して結果を報告し、ユーザーの判断・資料・操作が必要な事項だけを確認する。
回答できなかった確認は、UWIとして保存される。

### 対話型

エージェントへ作業を直接依頼する。
達成したい目標と対象リポジトリを伝えれば、必要な調査・実装・検証の構成も任せられる。
処理中のprocess-wiにも、同じリポジトリの目標を追加指示として伝えられる。
目的や範囲の選択が残る場合は候補と推奨案を確認し、確定した目的内の技術判断はエージェントが進める。
エージェントは調査・計画・実装・検証・公開まで進め、ユーザーだけが決められる事項を着手前に確認する。

### 自律型

`atk wi process-loop`を常駐させ、依頼したい内容をAWIとして登録する。
登録したAWIは、調査・計画・実装・レビュー・公開まで自動で処理される。

処理するリポジトリを登録し、ホームなどから起動する。
相対パスや`~`は絶対パスへ解決される。

```bash
atk config repos add ~/project-a --edit allow --process-loop
atk config repos add ~/project-b --edit allow --process-loop
atk config repos list
atk wi list --configured-repos
atk wi process-loop
```

登録していないリポジトリへもAWIを登録できる（常駐実行の処理対象にはならない）。
直接編集してよいリポジトリは`--edit allow`、AWIだけを投入するものは`--edit awi`、編集しないものは`--edit deny`で登録する。
未登録は編集不可として扱われ、編集ツールと`git commit`が拒否される。
編集を依頼した未登録リポジトリは、エージェントが登録内容を確認してから設定を進める。
cloneの無い対象も`atk config repos add github.com/example/project --edit awi`のように登録できる。
検証OSを限定する場合は`atk config repos set ~/project-a --platforms windows`のように指定する（未指定はLinuxとWindows）。
ほかの配列設定は`atk config add serve_plans_remote_hosts remote-host`と`atk config remove serve_plans_remote_hosts remote-host`で要素単位に操作できる。
dotfilesリポジトリでは自動で専用worktreeを使い、ほかのリポジトリでworktreeを分ける場合は`--worktree[=NAME]`を指定する。
統括担当をCodexにする場合は、起動前に`atk config set orchestrate_model codex:sol/medium,claude:opus[1m]/medium`を実行する。

`/agent-toolkit:add-awi-by-user`でAWIを登録している間や、未回答のUWIがある間は、処理の開始を最長10分程度待つ。
待機理由と期限は端末に表示される。
すぐに始めたい場合は、process-loopを動かす端末（tmuxではそのペイン）でEnterを押すと今回だけ待機を解除できる。

常駐中の操作は次のコマンドで行う。

```bash
atk wi process-loop status
atk wi process-loop abort
atk wi process-loop instruct '次の処理へ適用する指示'
```

`abort`は処理中のセッションが終わった時点でループを止める。
`instruct`は次の処理へ1回だけ指示を渡す。
中断した処理を再開する場合は、`atk wi process-loop --resume <session-ID>`へ統括担当の会話IDを指定するか、`--auto-resume`で保持された未完了の対象を再開する。

常駐させずに登録全体を1回だけ処理する場合は、`atk wi process-loop --once`を実行する（会話からは`/agent-toolkit:process-wi all-repos`）。
対象が0件なら直ちに終了し、未完了の対象が残ると非0で終了するため、表示された記録を確認してから`atk wi process-loop --once --auto-resume`で再開する。

CIの失敗だけを1回確認してAWIとして登録する場合は、`atk wi check-alerts --target-repo <リポジトリの絶対パス>`を実行する（省略時は現在の作業ツリー）。
AWIの処理は始めず、取得に失敗した場合は理由を示して終了コード1で終わる。

### まとめ処理型

常駐実行を起動せず、数件のAWIがたまった時点で対象リポジトリの会話から`/agent-toolkit:process-wi single-lane`を実行する。
同じセッションの中で調査から実装、レビュー、公開まで進む。
並列のレーンへ分けて処理する場合は`/agent-toolkit:process-wi`を実行する。

### 定期実行型

ログの点検のように、決まったスキルを一定の間隔で実行させたい場合は、cronなどへ`atk run-skill`を登録する。
`atk run-skill`は指定したリポジトリで指定したスキルを1回実行し、結果を報告用UWIとして保存する。
モデルの選択、利用上限の解除待ち、多重起動の防止、時間上限（省略時6時間）、ログの保存はコマンドが行う。

cronは端末と異なる`PATH`で起動するため、`atk`・`claude`・`codex`の場所を`PATH`へ含める。
標準出力を捨てると、失敗時だけ標準エラーの失敗行がcronのメールで届く。

```bash
PATH=/home/user/.local/bin:/usr/local/bin:/usr/bin:/bin
0 3 */3 * * atk run-skill --target-repo /home/user/repo my-skill >/dev/null
```

終了コード0は報告用UWIの保存（upstreamがあればpush）まで成功したこと、1は報告の保存に失敗したこと、124は時間上限に達したことを示す。
ログは`atk config get state_dir`が示すディレクトリの`run-skill/`に保存される。
未完了の実行入力とログは保持し、完了済みの記録とログは最終更新から30日を過ぎた実行開始時に回収する。
破壊的な操作など判断が要る対応は実施されずにUWIとして届く。
回答済みの承認は、次に起動するprocess-wiまたは同じスキルの定期実行が、そのスキルの手順で実施する。
結果報告への回答は、次回の同じスキルの実行が扱う。
オプションは`atk run-skill --help`で確認する。

報告の保存に失敗した場合（終了コード1）は、標準エラーが示すログを開いて次の手順で保存を完了する。

1. ログの「実行識別子」を控える
2. `RUN_ID`を控えた実行識別子へ置き換え、`atk run-skill --resume RUN_ID`を実行する
3. 元の会話・実行ID・報告先・batchの準備範囲・残時間を引き継いだ続行が、報告の同期を確認して成功することを確認する

再開時はスキル名・対象・cwd・引数・時間上限を変更しない。
完了済みの実行を再度指定しても、会話の再起動は行わない。

再開できない場合は、新しい会話でログの所在・実行識別子・スキル名を伝えて調査と復旧を依頼する。
