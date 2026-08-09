# 99-claude-code.md: Claude Code固有事項

Claude CodeのツールAPI、権限評価、環境依存の既知事象、委譲起動の制約、
セッション記録とフック、公式資料の参照先を扱う。

## ツールAPIと権限

- `AskUserQuestion`の`options`は1問最大4件。判断材料は同一ターン内のテキスト出力でまとめてから発行する
- ユーザーへの質問・承認待ちでターンを終える場合は必ず`AskUserQuestion`を使う（地の文の問いかけのみで終えない）
- 複数工程・複数完遂条件を伴う起動プロンプトの場合は`TaskCreate`で各工程を独立タスク化し、
  全タスクが`completed`になるまで完了報告を発行しない
- 100行超の連続ブロック置換は`Edit`の`old_string`にブロック全体を含めるか行範囲スライスを使う
- plugin資源のrootを再解決する場合、導入版とrootは`~/.claude/plugins/installed_plugins.json`の
  `installPath`から取得する。`~/.claude/plugins/data/`配下はplugin本体の展開先ではない
- 自律実行中は計画立案モードへ遷移せず、計画ファイルを直接作成する。
  同モードの終了時には利用者の承認要求が発生するが、自律実行では応答を得られず工程が止まる。
  協調実行では従来どおり同モードを使ってよい
  （厳守規定。自律実行の進行が承認待ちで停止し、依頼された工程が完了しない）
- `run_in_background=true`の長時間ジョブは`Monitor`で全行取得するか完了マーカーを`grep -q`で待つ。
  マーカー未生成の背景タスク完了検出目的では`Monitor`を起動しない。
  ツール側で背景実行を指定したコマンドはシェル側を前景のまま保ち、末尾の`&`や`nohup`を重ねない。
  二重に背景化するとシェルの終了が完了通知となり、実ジョブの完了を検知できない
- 完了までに時間を要するコマンドは、Bashツールのタイムアウト引数を上限まで引き上げた前景実行を
  既定とする。上限時間内に収まらない場合は、実行対象を限定して複数回の前景実行へ分ける
- 1つの応答内で先頭のツール呼び出しがブロック・エラーでキャンセルされると後続の独立呼び出しも連鎖キャンセルされる。
  各呼び出しの成否を`git diff`・`Read`で個別確認してから次工程へ進む
- auto modeでツール呼び出しが拒否された場合、推測でフラグ追加・迂回を試みず`agent-toolkit:agent-standards`
  `references/auto-mode.md`を参照してカスタムルール追加の要否を判断する
- 権限設定によりツール呼び出しが拒否された場合、迂回を試みる前に有効な設定ファイルを`Read`して
  拒否・許可ルールを確認する。対象は`/etc/claude-code/managed-settings.json`・
  `~/.claude/settings.json`・リポジトリ直下の`.claude/settings.json`・`.claude/settings.local.json`とする。
  評価はdeny・ask・allowの順で最初の一致が結果を決めるため、
  拒否ルールに該当する対象は許可・参照範囲の追加では解消しない（努力目標）
- フックがセッション状態の不足を理由にブロックした場合は、
  `{tempdir}/claude-agent-toolkit-{session_id}.json`を`Read`し、当該状態と他の記録済み状態を実測する。
  記録契機が発生していない場合は、同じ委譲の再実行以外の対処を選ぶ（努力目標）
- 読み手または委譲先が実行する手順として外部ツール・MCPツールを記述する前に、
  `ToolSearch`でツールスキーマを照会し実行を伴わずに確認する（努力目標）

### 環境依存の既知事象

特定の実行環境でだけ観測される事象と、その条件下での対処を置く。

- 判定条件はシステムプロンプトの環境情報で稼働モデルがFableと確認できることとする。
  観測事象はツール呼び出しが後続するターンの地の文が表示されないことである。
  対処として、`AskUserQuestion`の判断材料を`question`本文または`options`の`preview`欄へ自己完結で含める

## 委譲起動時の厳守事項

Claude Codeで委譲を起動する場合の実装手順は、`agent-toolkit:delegation`の
`references/claude-code-runtime.md`が定める。
本節には、委譲スキルを経由しない起動でも成立させる厳守規定だけを置く。

- AgentまたはTask起動では`name`パラメーターを渡さない。
  `name`付きbackground起動で完了通知が配送されず停滞する事象を実測しており、
  技術的な不成立に該当するため厳守規定とする

## セッション・フック

- セッション記録（`~/.claude/projects`配下）を集計対象とする場合、同一事象が親セッションと
  子セッションの記録へ重複して現れる構造を先に確認し、重複を除外したうえで件数を確定する（努力目標）。
  記録階層の構造と用途別の数え方は`agent-toolkit:agent-standards`の
  `references/session-records.md`が定める
- `agent-toolkit:exit-session`が自身をホストするClaude Code本体を終了する場合は、
  親子関係・実行ファイル・コマンドラインを照合して一意に特定した単一PIDを所有権確認済みとして扱う

## 公式リファレンス

スキル新規作成・hook実装では公式マーケットプレイス（`anthropics/claude-plugins-official`）の
`skill-creator:skill-creator`・`plugin-dev`各スキルを参照する。
各スキルの`references/`に記載のない仕様と、記載と認識が相違する事項は公式ドキュメントで確認する。
参照先は`https://code.claude.com/docs/ja/`配下（`memory.md`・`skills.md`・`sub-agents.md`・
`hooks.md`・`plugins.md`・`plugins-reference.md`など）とする。
列挙にないページを参照する場合は、ドキュメントインデックス（`https://code.claude.com/docs/llms.txt`）を
取得して対象ページのURLを特定する。インデックスが列挙するURLは英語版
（`https://code.claude.com/docs/en/<page>.md`）のため、日本語版を読む場合は言語部分を`ja`へ置換して用いる。
`WebFetch`はページの特定と概要の把握に使用してよい。
公式資料の文言、値の一覧、表の内容を成果物へ引用する場合は、同じURLの生本文を管理対象一時領域へ保存する。
保存後に該当箇所だけを検索し、生本文を典拠として引用する。
