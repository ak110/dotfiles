# rules-main.codex.md: Codexのメインエージェントだけに適用する規範

本文書はCodexのメインエージェントだけに適用し、`agent-toolkit/rules/`配下の共有規範と同じ拘束力を持つ。
Codex固有の公開能力と共有規範との差分を扱う。

## 言語

- ユーザーへの説明、確認、要約は日本語の書き言葉で行う
- 英語のコマンド、識別子、エラーメッセージには、必要に応じて意味又は目的を日本語で補足する
- 五段動詞の縮約形、ら抜き言葉、非標準的な文法を使わない

## Codex固有の入出力

- 全文取得は1ファイルにつき1回の実行セルで行う。複数取得を同じセルへ集約する場合は、内側の各最大出力量と付加分の合計を上回る`functions.exec`の`max_output_tokens`を先頭の`@exec`で明示する
- 最大出力量を確定できない検索、全件取得及び全文取得は別の取得と同じセルへ集約しない。切り詰めを通知された出力は読了、網羅性又は件数の根拠にしない
- 明示指示がないOfficeファイルは原本を直接変更せず、CSVはUTF-8 BOM付きで保存する

## Codexのplugin root解決

- agent-toolkitのスキルはplugin marketplaceが導入した実体の`<plugin root>/skills/<スキル名>/SKILL.md`を読む
- `<plugin root>`は`codex plugin list --json`の`installed`配列から`name`が`agent-toolkit`の要素を選び、Codexホーム、`marketplaceName`、`name`及び`version`から組み立てる。Codexホームは`CODEX_HOME`が設定済みならその値、未設定なら`~/.codex`とする
- 組み立てた絶対パス配下の`skills/`を確認し、コマンド失敗、対象要素の欠落又はroot不在では固定パスを推測せず呼び出し元へ差し戻す
- 起点のroot確定はホストのplugin導入情報だけから`SKILL.md`読取前に1回行う。読取済み`SKILL.md`の絶対パスからplugin資源rootを再解決する処理を、起点の確定へ循環利用しない
- 公開サブコマンドがないplugin内部資源は、読取済みのagent-toolkitスキルの絶対パスから現行plugin rootを再解決する
- プロジェクト直下の`.agents/skills/`、`AGENTS.md`がない場合の`CLAUDE.md`及び作業に該当する`.claude/rules/`を読む。`~/.codex/agent-toolkit/rules/`とdotfiles固有スキルはClaude Code側原本へのリンクとして扱う

## Codexホスト契約の適用

Codexではsystem、developer、userのホスト命令階層を常に優先する。`AGENTS.md`、プロジェクト指示及び共有規範は配送されたroleの範囲で適用し、`01-agent.md`の優先順位はホスト階層の適用後に残る同一role内の順位として読む。公開能力又は個別ツール契約と共有規範が異なる場合は、ホスト契約を優先する。

ツール前の短い`commentary`を求めるホストでは、共有規範の事前説明抑制にかかわらず送る。コード評価を伴うコマンドでは、処理、読取対象、確認目的を説明する。承認対象では、内容と影響範囲、復元方法を実行前に説明する。

会話圧縮後は、一時対象、固定集合、保留、承認、当初目的、確定済み要件及び残る完成条件を、対象リポジトリ、キュー管理リポジトリ又はホストの正本から再解決する。内部要約の言い換えを出所に代用しない。

`atk agents-exit-session`が現在のCodex本体を停止できる場合は、そのツール呼び出しをsession終端とし、停止後の`final`を前提にしない。

Codexの`list_agents`が対象を`running`と返す間は、差分、HEAD、更新時刻、無応答又は経過時間から停滞を推定して`interrupt_agent`を実行しない。中断はユーザーの明示要求、終端若しくは失敗への遷移又はタスク契約のキャンセル指定に限る。

回答期限を提供しないDefault modeでは協調モードの質問をユーザーへ直接提示して待つ。自律モードでは質問を発行せずUWIへ記録して暫定判断で続行する。

Codexの委譲待機はホストの`wait_agent`で終端を観測する。`wait_agent`がある場合は待機表明だけでturnを終えず、完了通知だけを提供するホストでは共有規範の再開経路を使う。
