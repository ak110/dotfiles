---
name: verify
description: >
  dotfilesの`atk`のCLI（WIキュー、計画の保存と取得、設定）を、実ユーザーのデータから隔離した環境で実プロセスとして起動・操作し、
  証拠を取って後始末する手順と機能一覧。
  `atk`のCLIの挙動（`agent-toolkit/agent_toolkit/_atk/`など）を変えた後、commit前に変えた機能を実際に動かして確かめる時に使う。
  CLIを使わないhook・MCP・画面の確認は、既存の隔離手順へ案内する。
---

# dotfilesの`atk` CLIの検証

検証対象のworktreeの`atk`を、実ユーザーのホーム、設定、private-notes、依存環境から切り離した作業領域で、実プロセスとして動かす。
本書の操作と証拠の保存はLinuxのCLIを対象とする。
`atk serve`の画面は`develop-dotfiles`の`references/browser.md`、テストの選び方は`develop-dotfiles`の`references/verification.md`「変更範囲の検証の値」が扱う。
このスキルの生成と保守の手順は`agent-basics:verify-skill`にある。

以下の`<sandbox>`は`<検証対象のworktree>/.claude/skills/verify/scripts/atk-sandbox.sh`の絶対パス、`<S>`は作業領域、`<E>`は証拠の置き場所の絶対パスである。

## 起動

1. `<S>`と`<E>`を作る。
   `<S>`はGitのリポジトリを含むため、`agent-toolkit:atk-mode/playbooks/managed-temp.md`「一時root直下に作成する領域」のとおり、一時root直下に`mktemp -d '<一時root>/verify-atk-XXXXXXXX'`で作る。
   `<E>`は後始末の後も読むため、セッションのmanaged-tempの中に`mktemp -d`で作る。
2. `<sandbox> setup <S> <E>`を実行する。
   `<S>`に、隔離したホームとXDGの各ディレクトリ、private-notesとその送り先の裸のリポジトリ、git設定、検証対象の`agent-toolkit/scripts/atk`を指す`atk`を作る。
   隔離に使う環境変数は`<S>/sandbox.env`に書き、`<E>`へ写す。
   uvとmiseは、取得し直しとmiseの信頼登録の不足を避けるため、実環境の取得物と状態を使う。
   最後に`atk info`で依存環境を`<S>/runtime-data`へ準備し（uvのキャッシュからのリンクで数秒）、導入の出力は`<E>`へだけ残す。
3. 次の「状態確認」が成功したら使える状態とする。
   CLIはコマンドごとに終わり、常駐するプロセスを起動しない。

## 状態確認

`<sandbox> doctor <S>`を、最初の操作の前と、操作が想定外の結果になった後に実行する。
`atk info`のplugin rootが検証対象の`agent-toolkit`、`atk config get private_notes`が`<S>/private-notes`、そのoriginが`<S>/notes-remote.git`に全て一致する時だけ成功する。
失敗した時は操作に進まない（private-notesの解決を誤ると、実ユーザーのprivate-notesへcommitとpushが届く）。

## 操作

`<sandbox> run <S> <ラベル> <コマンド...>`で1コマンドずつ実行する。
コマンドはGitの作業ツリーの外の`<S>/work`をcwdとし、`<S>/sandbox.env`の環境変数だけを持つ環境で動く。
`atk`は検証対象のworktreeの起動部分を、`git`は`<S>/gitconfig`を使う。
対象リポジトリは`--target-repo github.com/example/verify`のように実在しないURLで指定する。
原稿は`<S>/work`へ書いて`--body-file`で渡す。

## 証拠

`run`と`doctor`はコマンドごとに、引数、標準出力、標準エラー、終了コードを`<E>/<連番>-<ラベル>.{cmd,out,err,rc}`へ保存する。

- 公開のサブコマンドだけで操作し、内部の関数やテストの補助で得た結果は証拠から外す。
- 状態を変えた操作は、`成功:`で始まる行と終了コード0に加えて、送り先の裸のリポジトリの履歴で確かめる。
- `--dry-run`では送り先の履歴が変わらないことを確かめる。

## 後始末

`<sandbox> cleanup <S>`を、成功した後と、失敗した反復の後に実行する。
環境変数`HOME`が`<S>/home`のプロセスが残っていれば、一覧を出して何も消さずに失敗する。
その時は残ったプロセスを起動した時の識別子で止めてから再実行する（名前で止めない）。
残りが無ければ`<S>`だけを消して不在を確かめ、`<E>`に残った証拠の件数を示す。

## 機能一覧

CLIは「起動」と「状態確認」の後に対象の操作を実行する。
CLI以外を確かめる場合は、次の表のうち該当する入口で、使った条件と回収の結果を別に記録する。
既存のテストが応答を判定する場合は、応答本文を取り直さず、テスト名・合否と確かめた範囲を保存する。
`<名前>`は前の段の出力から取る値である。

| 機能 | 隔離した代表操作と期待結果 |
| --- | --- |
| CLI | 以下のWI・計画・設定を操作し、結果と保存先を比べる |
| `atk serve` | [画面とブラウザー](../develop-dotfiles/references/browser.md#画面とブラウザー)の一時データでWIを保存し、画面と保存本文が一致することを確かめる |
| hook | `<sandbox> run <S> hook env AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START=false uv run --no-cache --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py agent_toolkit/hook.py rules_context_codex < <S>/work/hook-input.json`で隔離したSessionStart入力を渡し、終了0と通知JSONを確かめる |
| MCP | 下記の隔離したstdioのテストで、公開ランチャーの応答と転送・管理サービスの回収を確かめる |

MCPの代表操作は、リポジトリルートで次を実行する。

```bash
uv run --frozen pytest agent-toolkit/agent_toolkit/_agents_server/mcp_transport_test.py::test_launcher_records_stdin_close_exit
```

公開ランチャーの`initialize`と`tools/list`、標準入力を閉じた後の転送の終了、管理サービスを停止した後の終了記録を確かめるテストである。
モデルや業務ツールの実行はこのテストの対象外とする。

hook入力には`hook_event_name: SessionStart`、`source: startup`、合成の`session_id`、`cwd`として`<S>/work`を持つJSONオブジェクトを使う。

### WIの投入から採用まで

AWIをinboxへ投入し、一覧と本文で確かめ、処理中を経て採用で終える。
到達する操作は`atk wi add`、`list`、`show`、`start-processing`、`adopt`である。

1. `<S>/work/body.md`へH1と本文を持つ原稿を書く。
2. `<sandbox> run <S> add-dry-run atk wi add --dry-run --target-repo github.com/example/verify --body-file <S>/work/body.md`を実行する。
   `成功:`の行と原稿の冒頭が出る。
3. `<sandbox> run <S> add atk wi add --target-repo github.com/example/verify --body-file <S>/work/body.md`を実行する。
   `成功: 1件をinboxへ投入した`と保存先`<S>/private-notes/<年>/<月>/<WI名>`が出る。
4. `<sandbox> run <S> list atk wi list --target-repo github.com/example/verify`を実行する。
   `<WI名>`が`[inbox/`で始まる状態で並ぶ。
5. `<sandbox> run <S> show atk wi show <WI名> --target-repo github.com/example/verify`を実行する。
   `state: inbox`と原稿の本文が出る。
6. `<sandbox> run <S> start atk wi start-processing <WI名> --target-repo github.com/example/verify`を実行する。
   `成功: 1件をprocessingへ移した`が出る。
7. `<sandbox> run <S> adopt atk wi adopt <WI名> --note=検証で採用 --target-repo github.com/example/verify`を実行する。
   `成功: 1件をadoptedへ移した`が出る。
8. `<sandbox> run <S> adopted atk wi list --state adopted --target-repo github.com/example/verify`を実行する。
   `<WI名>`が`[adopted/`で始まる状態で並ぶ。
9. `<sandbox> run <S> remote-log git -C <S>/notes-remote.git log --stat`を実行する。
   初期化の後に投入、処理開始、採用の3件のcommitがあり、どれも`<WI名>`の1ファイルを変えている（dry-runのcommitは無い）。
10. `<sandbox> run <S> remote-body git -C <S>/notes-remote.git show HEAD:<年>/<月>/<WI名>`を実行する。
   `state: adopted`と、`## 処理結果`の下に`メモ: 検証で採用`がある。

### 計画の保存と取得

作業中の計画を`atk plans commit`でprivate-notesへ保存し、`atk plans checkout`で取り出す。

1. `<S>/home/.claude/plans/19-1030_verify-sample.md`へH1と本文を持つ計画を書き、比べるための写しを`<S>/work/plan-original.md`に置く。
2. `<sandbox> run <S> plan-commit atk plans commit 19-1030_verify-sample.md`を実行する。
   `成功:`の行に保存先`<年>/<月>/19-1030_verify-sample.md`が出る。
   `<sandbox> run <S> plan-dir ls -A <S>/home/.claude/plans`の出力は空になる。
3. `<sandbox> run <S> plan-checkout atk plans checkout <年>/<月>/19-1030_verify-sample.md`を実行する。
   `成功:`の行に`<S>/home/.claude/plans/19-1030_verify-sample.md`が出る。
   `<sandbox> run <S> plan-diff diff -- <S>/work/plan-original.md <S>/home/.claude/plans/19-1030_verify-sample.md`は出力なしで終了コード0になる。
4. `<sandbox> run <S> plan-remote-log git -C <S>/notes-remote.git log --stat -1`の出力で、`plans/<年>/<月>/19-1030_verify-sample.md`を足している。

### 設定の取得と変更

1. `<sandbox> run <S> config-before atk config get codex_fast_mode`を実行する。
   `auto`が出る。
2. `<sandbox> run <S> config-set atk config set codex_fast_mode true`を実行する。
   `成功:`の行が出る。
3. `<sandbox> run <S> config-after atk config get codex_fast_mode`を実行する。
   `true`が出る。
4. `<sandbox> run <S> config-file cat <S>/home/.config/agent-toolkit/config.json`を実行する。
   `"codex_fast_mode": "true"`が保存されている（値は文字列で保存される）。

### 誤りやすい点と対象外

- ホストのディスクの空きが容量の10%を割ると、`atk`が標準エラーへ空きの警告を書く。
  隔離した一時rootの使用量とは無関係のため、機能の失敗と区別する。
- 計画は`~/.claude/plans`を読み書きするため、ホームを隔離しない実行は実ユーザーの計画に触れる。
- 委譲を起動する機能（`atk agents`、`atk commit`、`atk run-skill`、`atk wi process-loop`）は隔離したホームに資格情報が無いため対象外とする。
- Windowsの`atk.cmd`は対象外とする。
