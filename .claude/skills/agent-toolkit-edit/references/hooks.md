# hookの実装の規約

agent-toolkitの配布物と個人用のhook、`atk`の出力を新設・変更する時の、このリポジトリでの差分の規約である。
一般の基準は`agent-basics:writing-standards/references/claude-hooks.md`と`agent-basics:writing-standards/references/claude-hooks-block-warn.md`にある。
本書は、このリポジトリのモジュールとファイルの名前、置き場所、互換の引数、状態の単位だけを持つ。

## hookスクリプトの基本プロトコル

- `SessionStart`は`agents_server`の委譲先でも発火し、`SubagentStart`は`Agent`ツールのサブエージェントの起動時だけ発火する。
- `agent-toolkit/agent_toolkit/_hooks/rules_context.py`は、`SessionStart`で委譲先を判別してメイン向けの案内を除く。
  `SubagentStart`では、委譲先の入口`agent-toolkit/skills/atk-mode/playbooks/subagent.md`を読む指示と`atk`の所在を足す。
  両通知の内容は上記の案内と警告に限る。
  委譲先の判別には環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`と`AGENT_TOOLKIT_OWNER_SESSION`を使う。
- `Stop`と`SubagentStop`の判別は`agent-toolkit:atk-mode/playbooks/host-claude-code.md`「委譲先での発火」に従う。

## 同期hookの所要

[Claude Code公式のhook仕様](https://code.claude.com/docs/en/hooks#run-hooks-in-the-background)では、既定の同期hookは完了まで実行を止める。
[タイムアウト](https://code.claude.com/docs/en/hooks#timeouts)はhookの種類・イベントで異なり、個々の処理に許容される所要の目安ではない。
圧縮後の要約は、WI `20261123-012545-001.md` に記録された事例では内部の15秒上限に達し、手動では約45秒で成功した。
入力の量で所要が大きく変わる処理は同期hookから外し、必要な結果を得る手動操作か、結果を回収する主体が明確な背景処理へ移す。

## フック実装の配置先（個人フックと配布物）

dotfiles固有の運用（配布先の構成、個人の命名規則など）を前提にするhookは個人のhookへ、前提を持たない汎用の機能はプラグインへ置く。
似たチェックが既にある側へ統合する（努力目標。同じ判定を2か所で保守しないため）。

- `pytools/claude_hook/pretooluse.py`（個人のhook）は、chezmoiで自分のアカウントの`~/.claude/settings.json`にだけマージされる。
  新しいツール名を対象にする時は、`share/claude_settings_json_managed.posix.json`と`share/claude_settings_json_managed.win32.json`の`matcher`を確かめる。
- `agent-toolkit/`（プラグイン）は`.claude-plugin/marketplace.json`で他のエンドユーザーにも配るため、変えたら版数を上げる。
- `hooks.json`と`share/claude_settings_json_managed.*.json`が指すスクリプトの改名・移動・削除は、起動パスの互換性維持に従う。
  定義は`agent-basics:writing-standards/references/claude-hooks.md`「hookスクリプトの基本プロトコル」にある。
  配布先で導入エラーになる配置は新しい配布物に残さず、旧版の実体の保持か更新時の移行処理で、旧方式の呼び出しを保つ。
- 同じイベントへhookを足す時のエントリーポイントの集約（同節）は、matcherの条件が互いに重ならない登録なら満たすものとして扱う。
  入力契約の違いなどで統合が難しい時は、理由を書いてエントリを分ける。

## agents_serverのツール呼び出しを判定するhook

`agents_server`はユーザーの承認、対話の入力、認証の更新、一覧の取得などの操作を公開せず、実行中のターンの中断だけをセッション単位の`kill`として公開する。
これを判定するhookは、次の範囲に限る。

- PreToolUseは、`send_message`と`kill`の保存済みのセッションの妥当性と、`<役割名>.subagent.md`で定義した実行命令を伴う起動かを確かめる。
- `start`の`delegate`、`explore`、`write`のmodeへ、引用枠の外で`<役割名>.subagent.md`の手順による実行命令を直接渡した場合は、タスクの起動形式へ直すよう遮断する。
  `Agent`と`Task`の実行命令では、1行目の正式な命令と事前に宣言した入力のほかの行を含む場合に遮断する。
- 文書の読解、引用、比較の対象としてのパスの参照、引用の中の実行命令の例、参照だけでは用途が決まらない場合は通し、会話の文脈を推測した遮断と警告をしない。
- `start`の入力の検証（modeごとの必須項目の不足や不正な混在）と、`send_message`・`kill`の入力形式の検証（`prompt`、`instruction`、`extra_params`、`session_id`の不備）は実行基盤に任せ、原因と次の操作はツールのエラー応答で伝える。
  hookにツールと同じ受理条件を持たせると、ツールの入力形式が広がった時に正当な入力を誤って警告するためである。
  実行権限の値はそのままツールへ渡し、新しいターンを始めない`wait`は判定しない。
- PostToolUseは、成功した`start`（旧名で記録した開始を含む全てのmode）のcwdと、`wait`、`send_message`、`kill`の後のセッション状態を記録する。

## 複数hook共存時の識別子

`atk-auto`のタグの`source`属性には、agent-toolkit由来のメッセージなら接頭辞を付けない生成元名を、それ以外の生成元なら`<所有者>/<生成元>`の形を書く。
XMLタグの境界と属性の書き方は`agent-basics:writing-standards/references/claude-hooks-block-warn.md`「エージェント宛てメッセージの標識」に従う。

## 遮断と警告の選択

遮断か警告か、反復の通知の抑え方は`agent-basics:writing-standards/references/claude-hooks-block-warn.md`「遮断と警告の選択」が定め、本リポジトリの実装は次のとおりである。

- 反復の抑止は`agent_toolkit._hooks.notice`の`formatter`と`warning_formatter`が行い、単位は同じ会話、呼び出し主体、操作範囲である。
- 既存の呼び出し元が渡す`removable_cause`、`escalate_on_repeat`、`summary`は、入力の受理形式を保つために受け付けるが、回数の通知と遮断への格上げの判定には使わない。

## 通知本文の整形

hookの通知は共有のformatterで作る。

```python
from agent_toolkit._hooks.notice import formatter

_llm_notice = formatter("myplugin/myhook")
```

- block通知は`block_formatter`、warn通知は`formatter`か`warning_formatter`で作り、解消の手段を本文へ混ぜずに`fix`引数へ渡す。
  `fix`が空か空白だけなら`ValueError`を送出し、整形関数は本文の末尾へ`次の操作: <fix>`の行を足す。
- `agent-basics:writing-standards/references/claude-hooks-block-warn.md`「通知本文の整形」は、次の本文にも適用する。
  対象は、TypeScript製のhookの`agent-toolkit/hooks/automated_message.ts`の`automatedMessage`が包む本文と、`agent-toolkit/agent_toolkit/_common/next_action.py`の次の操作である。
- 閾値と記録先は`agent-toolkit/agent_toolkit/_hooks/notice.py`で管理する。

## 出力フィールドの使い分け

- イベントごとの出力契約は、`agent-toolkit/agent_toolkit/_testing/hook_output_contract.py`が公式のHooksリファレンスからJSON Schemaで定義する。
  `agent-toolkit/agent_toolkit/_hooks/output_contract_test.py`が、登録済みの全てのhookの出力をその契約で検証する。
- 永続のログは`_hooks.stop_session.append_stop_log`などの専用のAPIで書く。

### UserPromptSubmit

- Claude Codeの`UserPromptSubmit`のpayloadは、セッションの現在の名前を`session_title`に持つ。
- Pythonのコマンドhookは`hookSpecificOutput.sessionTitle`でタイトルを設定し、注記は同じオブジェクトの`additionalContext`に入れて返す。
- 現在の名前が空か、前回自動で設定した名前と一致する時だけ更新し、ユーザーが手で設定したタイトルを保つ。
- 命名用の種別は`PostToolUse(Skill)`と手動の起動時の`UserPromptSubmit`が通常の状態に記録し、タイトルは`process-loop`の環境の印と計画名から決める。
- セッションタイトルの設定はClaude Codeに限る。

### 委譲先で配送本文を検証する

ホストを起動せず、[develop-dotfilesのテスト配置](../../develop-dotfiles/references/verification.md#テスト配置)の `isolated_development_state(root)` 内で `user_prompt_submit` に合成JSONを渡す。
通常発話の対照は子から `AGENT_TOOLKIT_OWNER_SESSION` を除いて `AGENT_TOOLKIT_DELEGATED_SESSION=0`、委譲先の対照は別の隔離先で `AGENT_TOOLKIT_DELEGATED_SESSION=1` とする。
各回の標準出力・標準エラー・終了コードを分けて保存し、通常発話向けの注記が通常発話の対照だけに現れることを確かめる。

## atkの実行結果出力

- `atk`のサブコマンドと出力の行を足す・変える時は、接頭辞の文字列を直接書かず、`agent-toolkit/agent_toolkit/_atk/outcome.py`の接頭辞と区分を使う（末端のサブコマンドの区分の登録の欠けは`atk_help_test.py`が検出する）。
- 出力する行は、実行の結果、失敗と警告の詳細、次に行う操作に限る。
- 末端のサブコマンドの登録か区分を変えた時は、子プロセスの標準出力が成功のメッセージより先に出る場合も含め、結果の行が区分に応じた出力先と順序に従うことをテストで確かめる。
- 設計の目的と見送った代替案は`docs/development/design-cli-output.md`「atkの結果行の接頭辞と出力先」にある。
