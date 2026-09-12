# 99-claude-code.md: Claude Code固有事項

Claude CodeのツールAPI、権限評価、環境依存の既知事象、委譲起動の制約を扱う。

## ツールAPIと権限

- Claude Codeは地の文の無い応答を可視出力の欠落として扱い、応答の再生成を要求する。
  `agent-toolkit:delegation`の`references/waiting-and-monitoring.md`が定める待機表明の回など、
  地の文で伝える内容が無い回は、記号1文字（`…`）だけを出力して当該要求を満たす
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/rules/99-claude-code.md：ツールAPIと権限：2026年9月1日」にある
- auto modeまたは権限設定でツール呼び出しが拒否された場合、推測でフラグ追加・迂回を試みず
  `agent-toolkit:writing-standards`の`references/auto-mode.md`を参照して対応を判断する
- エージェント定義（`.claude/agents/`配下・`~/.claude/agents/`配下・プラグイン配布分）はセッション起動時に読み込まれる。実行中のセッションで新規作成・改訂した定義は、当該セッションの`subagent_type`から解決できない。frontmatterの`tools`・`model`・`effort`を省略した場合は、順にサブエージェントが利用できる全ツール・`inherit`・セッション値を継承するため、起動プロンプトの文言ではこれらの条件を固定できない
- Bashツールで`run_in_background=true`により背景実行したコマンドを停止する場合は、
  起動結果が返したタスクIDを`TaskStop`ツールへ渡す。
  前景起動が実行環境の判断で背景実行へ移行し、移行通知がタスクIDを示した場合も同様とする。
  `TaskStop`がツール一覧へ遅延提示される環境では、ツールスキーマの検索手段で定義を取得してから呼び出す。
  シェルの`kill`等でPIDを推測して停止しない
  （タスクIDとPIDの対応を取得できないため、推測は無関係なプロセスの終了を招き得る）
- 100行を超える連続したブロックを置換する場合は、`Edit`の`old_string`へ当該ブロック全体を含めるか、行範囲を指定した取得で対象範囲を確定してから置換する

## plugin資源のroot再解決

plugin資源のrootが失効したことを観測した場合、以後は保持済みのrootを再確認なしで再利用せず、
実行環境が提供するplugin導入情報から現行の導入版とrootを再解決し、
利用する資源の実在を確認してから後続処理へ渡す。

Claude Codeでは、導入版とrootは`~/.claude/plugins/installed_plugins.json`の`installPath`から取得する。
`~/.claude/plugins/data/`配下はplugin本体の展開先ではない。

Codexでは、実行中スキルのSKILL.md絶対パスから末尾成分（`skills/<skill-name>/SKILL.md`）を除いた
接頭部をrootとして導出する。導出したrootは配下資源の実在確認を経てから用いる。

## 役割上の区分と実行環境上の区分

規範が役割として定める委譲元と委譲先の区分は、Claude Codeがターン終端イベントと設定の継承を決める区分と一致しない。
フックの実装と改訂、及び規範の適用範囲を判定する場面では、どちらの区分を基準にするかを先に確定する。

- `agents_server`の`start`で起動した委譲先は、独立したClaude Agent SDKセッションとして動く。
  当該セッション自身のターン終端では`SubagentStop`ではなく`Stop`が発火する。
  `Agent`ツールで起動したサブエージェントのターン終端では`SubagentStop`が発火し、
  当該サブエージェント内で発火するフックの入力には共通入力の`agent_id`が入る
- `agents_server`の通常起動（`start`）は`setting_sources`へ`user`と`project`を渡すため、
  ユーザー設定、プロジェクト設定及びそこで有効なプラグインのフックが委譲先セッションでも読み込まれる。
  軽量起動（`start_explore`と`start_shell`）は`setting_sources`が空であり、これらを読み込まない
- `agents_server`の通常起動で起動した委譲先には、`agents_server`が`agent-toolkit/share/rules-subagent.md`の条文をシステム指示へ連結して渡す。
  `Agent`ツールで起動したサブエージェントには、`SubagentStart`フックが同じ条文を文脈へ追加する
- メインエージェントだけに適用する条文（`agent-toolkit/share/rules-main.md`と`agent-toolkit/share/rules-main.claude-code.md`）は`SessionStart`フックが文脈へ追加する。
  同フックは、環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`が`1`である場合と`AGENT_TOOLKIT_OWNER_SESSION`が設定されている場合に当該条文を追加しない。
  いずれも`agents_server`が起動した子だけが持つ印であり、後者はCodex backendの委譲先が前者を持たないための判定である
- 規範上は委譲先である主体が、実行環境上は`Stop`側のフックの対象になる。
  `Stop`側のフックを実装又は改訂する場合は、当該フックが求める処置を委譲先が実行できるかを個別に判定する。
  実行できない処置を求めるフックは、環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`が`1`であることを条件に委譲先を対象から除く
- 規範が最上位セッションへ限定する工程は、`Stop`が発火したことを自身が最上位である根拠にしない。
  `agent-toolkit:session-review`の起動と`SendMessage`の`to: "main"`による通知の宛先が該当する。
  判定には当該環境変数と自身の起動経路を用いる

`agents_server`の通常起動と軽量起動、及びStop側とSubagentStop側のフックは異なる動作をする。監査記録は`docs/development/audit-records.md`の「agent-toolkit/rules/99-claude-code.md：役割上の区分と実行環境上の区分：2026年9月4日」にある。

配布後の条文配送は次の手順で再検証する。Claude Codeを再起動した後の最上位セッションで、当該セッションのシステム指示を確認する。`agent-toolkit/rules/`直下の3ファイルと`agent-toolkit/share/rules-main.md`及び`agent-toolkit/share/rules-main.claude-code.md`が現れることを、この確認の合格条件とする。続いて同じセッションから`agents_server`の`start_explore`で委譲先を1件起動する。当該委譲先へ配送された規範ファイルの一覧を返させ、前記の5ファイルと`agent-toolkit/share/rules-subagent.md`のいずれもが現れないことを確認する。軽量起動は`setting_sources`を空とし、システム指示へプリセットを用いず固定の起動文だけを渡す。このためフックが追加する条文も`agents_server`が連結する条文も届かない。観測が本項と異なる場合は、当該差分を事象として本節の記述を是正する。

## 委譲起動時の厳守事項

Claude Codeの基本的な委譲起動では、`02-agent-operations.md`「基本委譲契約」節を適用する。
工程別モデル設定、複数主体調整、継続、停滞検知又は巻き取りが必要な場合の実装手順は、
`agent-toolkit:delegation`の`references/claude-code-runtime.md`が定める。
