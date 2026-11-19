# WIの登録と回答

## WI投入の独立実行

製品の変更・公開とは独立した改善要求は、エージェントが`atk wi submit`で別の投入実行へ引き継げる。
同時に確定した同一対象の要求は、識別子ごとのJSON objectを1つの配列にして渡す。
配列入力では要求ごとの受付票が配列で返り、すべての票を確認してから待機対象を分離する。
元の作業報告にある受付票は、要求を保存して担当へ渡したことを示す。
実際に登録されたAWIと、その投入実行の結果は別の報告で届く。

受付後の状態は、報告にある要求識別子を使って確認できる。

```bash
atk wi submit --show REQUEST_ID
```

未完了・未報告の要求は保持され、セッションの開始・終了でも再確認される。
状態照会で原因と再開先を確認し、成否が不明な要求は重ねて登録しない。
入力の詳細は`atk wi submit --help`を参照する。

## 登録方法

| 登録方法 | 選ぶ場面 |
| --- | --- |
| `atk wi add` | 依頼内容が既に固まっており、本文をそのまま登録したい |
| `atk wi add --batch` | 別環境の`atk wi show --all`の出力を複数件まとめて移行・復元したい |
| `/agent-toolkit:atk-mode add-awi-by-user <要求>` | 依頼内容を具体化し、必要な要件確認を対話で行って登録したい |
| `atk serve`の新規追加 | 画面から登録したい。種別「一括登録（show形式）」で移行・復元もできる |

通常の登録は、本文を書いたファイルを渡して`atk wi add --body-file <本文ファイル>`を実行する。
`atk wi add --cooldown-until '2026-10-11T12:00:00+09:00' --body-file <本文ファイル>`のように、指定時刻まで処理を始めないAWIも登録できる。
登録済みのAWIを直したい場合は、`atk wi hold <ファイル名>`で自動処理から外し、本文を直してから`atk wi unhold <ファイル名>`で戻す。
処理中の項目は保留せず、変更内容を新しいAWIとして登録する。

エージェントは目的・範囲・選好・認可など、ユーザーの判断が必要な点を確認してから要求を具体化し、意味の検収を経て登録する。
モードを選ぶ引数や、本文を書く前の一律の最終承認は不要である。
考慮漏れの指摘には原因を分析し、同じ理由の見落としへの処置と再発防止策を要求へ反映する。

## UWIへの回答と状態確認

未回答のUWIは`atk wi answer`で順に確認して回答できる。
ファイル名と回答を指定する場合は次の形式を使う。

```bash
atk wi answer <UWIファイル名> '<回答本文>'
```

エージェントが実施済みの対応への承認を求めるUWI（本文の先頭に`question_type: post-approval`）では、「その対応で問題無い」と回答すると保存時に完了する。
それ以外の回答は、次の処理でエージェントが元の作業へ反映する。
処理中の会話へ「UWI回答した」と伝えると、同じ実行で反映される。

項目の確認には次の指定を使う。
対象はカレントディレクトリのリポジトリで、全リポジトリを確認する場合は`--target-repo=all`を加える。

- `atk wi list --status=active`: 現在の項目
- `atk wi list --status=processable`: 自動処理へ渡せる項目
- `atk wi list --type=uwi --answered=no`: 未回答のUWI

## 処理対象と検証OS

`atk config repos`で登録済みリポジトリと属性を一覧表示する。
`edit`は編集の許可、`process_loop`はprocess-loop（`atk wi process-loop`）の対象、`platforms`はテストを実行するOSを表す。
`platforms`を省略した登録は`linux,windows`となり、リポジトリの内容から自動判定される値ではない。
テストをWindowsで実行する対象は、次のように変更できる。

```bash
atk config repos set github.com/ak110/gv --platforms windows
atk config repos set github.com/ak110/lc --platforms windows
```

この指定は他の属性を変更しない。
Linux上のエージェントは、その環境で可能なクロスビルドなどを確認し、テストの合否はWindowsのCIで判断する。
gv・lcはLinuxでも開発用のコンパイル確認ができるため、`platforms windows`はLinuxでの作業自体を禁止する設定ではない。
Linuxで行うテストも検証へ含めたい場合は`--platforms linux,windows`を使う。

## ブランチの系統

リリース済みの版を直す保守ブランチと次期版の開発ブランチのように、継続して作業が入るブランチが2つ以上あるリポジトリでは、それらのブランチを「系統」として登録する。
登録すると、WIが宛先の系統を持ち、process-loopが系統ごとに処理する。
系統を登録しないリポジトリの動作は変わらない。

```bash
atk config repos set ~/project-a --branches release-1.x,main --default-branch main --merge-from main=release-1.x
atk config repos list
```

- `--branches`: 系統にするoriginのブランチ名。
  originに無い名前は登録できず、登録にはローカルのパス（`--path`）が必要
- `--default-branch`: 系統を指定しないWIの宛先（既定の系統）。
  新しい要求の多くが入るブランチ（通常は次期版の開発ブランチ）を選ぶ
- `--merge-from`: `<取り込む側>=<取り込み元>`の組。
  取り込む側はリリースの前に取り込み元をマージする。
  保守ブランチの修正を次期版へ持ち込むため、通常は`<開発ブランチ>=<保守ブランチ>`とする

`--branches ""`で系統の登録を外せる（既定の系統と取り込み元も外れる）。
登録から外した系統を宛先に持つWIは、`atk wi list`で`blocked_reason=branch-unregistered`の着手不能になる。

WIの宛先は`atk wi add --target-branch <系統>`か本文frontmatterの`target_branch`で指定し、省略すると既定の系統になる。
登録済みのWIの系統は、`atk wi edit`で本文frontmatterの`target_branch`を書き換えて変更できる。
両方の系統に必要な修正は取り込み元の系統（保守ブランチ）へ1件だけ登録し、取り込む側のリリース前のマージで反映する。
`atk wi list`・`atk wi show --all`・`atk wi grep`は`--target-branch <系統>`で対象を限定できる。
`--target-branch=auto`は、現在の作業ツリーが追跡するブランチの系統に限定する。

process-loopは、リポジトリと系統の組を1つの処理対象とする。
各系統は、`atk`が登録パス配下の`.claude/worktrees/process-loop-<系統>`に自動で用意する作業ツリーで処理し、登録パスの作業ツリーのブランチは切り替えない。
CI失敗の監視は系統ごとにブランチを照会し、失敗した系統を宛先にAWIを登録する。
GitHub CopilotのレビューとDependabotアラートの確認は、既定の系統の処理だけが行う。
`/agent-toolkit:atk-mode process-wi`を手動で起動する場合は、処理したい系統を追跡する作業ツリーで起動する。

## process-loopと再開予約

`atk wi process-loop`は登録した対象を継続処理する。
各対象は独立して終了・検収されるため、別の対象が処理中でも、終了した対象の新着を次の実行へ渡せる。
`--once`は開始時の対象を各1回処理して終了し、未完了や所有待ちが残れば非0で終了する。

次回に再開する会話は、常駐が停止中でも予約できる。

```bash
atk wi process-loop resume SESSION_ID
atk wi process-loop resume auto
atk wi process-loop resume
atk wi process-loop status
atk wi process-loop resume-cancel
```

`auto`は保存した未完了会話を選び、値なしの`resume`は次の起動でホストCLIの会話選択画面を開く。
会話選択画面には対話端末が必要で、非対応のホストではIDか`auto`を指定する。
予約コマンド自体は会話を起動せず、後から受理した予約で置き換えられる。
予約はCLIの実起動で1回消費され、準備・更新・起動の前に失敗した場合は残る。
旧`--resume`・`--auto-resume`オプションは使わない。

`abort`は新しい対象の起動を止め、現在の実行が終了してから常駐を終了する。
稼働中の会話は強制終了せず、未消費の再開予約も保持する。
`instruct '追加指示'`は次に実起動する1会話へ渡し、`instruct-cancel`で取り消せる。
