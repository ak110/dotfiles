# 読込表の監査記録: 後続観測と再検証

[読込表の記録一覧](audit-records-read-triggers.md)から各方式の分類と観測を参照する。

全文取得方式の後続自然観測もClaude Code 2.1.311で、同じPythonの不具合調査と報告書作成を依頼した。
会話は終了0で最終応答を得た。
Write後の案内、資料のRead、再WriteとEditを観測した。
basics側原因分析資料の前半は出力が省略され、後半だけが取得範囲に記録されたため全文取得済みにはならなかった。
toolkit側原因分析資料は全文取得済みとなった。
未取得の資料が残り、モデル内部の判断や利用は観測できない。
全文取得方式の後続自然観測では、Claude Code 2.1.311に`/home/aki/dotfiles/.claude/worktrees/process-wi-lane-05/agent-toolkit`の2.228.0だけを`--plugin-dir`で指定した。
インストール済み`agent-basics@ak110-dotfiles` 1.4.0は`/home/aki/dotfiles/agent-basics`に登録され、そのSkillを読み込んだ。
Readは`/home/aki/dotfiles/.claude/worktrees/process-wi-lane-05/agent-basics`を対象とした。
当時の登録元とRead先の本文の同一性は確認していない。

後続自然観測時の`read_delivery.py` SHA256は`5401948b0b85a5e35f71c36506835e46a3985944cf064f35be6dda5fb08b92d6`である。
`read_triggers.py`は`e89e28d67bde492d15516738cc347b174dd57440450627ab19cbd23a84cd0969`である。
`rules_context.py`は`d57dfbd19f0e01e7a3d390f0b775ca0e2e22aebd4d7b191389f1e1fbd58c0364`である。
観測後に例外案内と不在資料向け案内を変更したため、後述の現行版と完全同一ではない。
正常資料の案内と全文被覆は変更せず、不在分岐は公開hook入力を用いた別の試験で確認した。

全文取得方式の別の制御観測では、1つのworktree内の2 pluginを明示し、許可ツールをReadとSkill、上限を120秒としてClaude Code 2.1.311で実行した。
終了0・result successだった。
登録は同じworktreeのagent-toolkit 2.228.0とagent-basics 1.4.0である。
`agent-basics:bugfix`のSkillと`agent-basics/skills/bugfix/SKILL.md`のReadの1資料を照合した。
Readは121行中121行で省略なし、本文SHA256は`301587105fd4b30d287e954ef3d263007c7787d7cf78d44c383c3cffcc10d494`だった。
Skill本文とRead本文は同じworktreeの資料と一致した。
制御観測の3実装sourceは後述の現行SHAと一致した。
自然会話や、同名pluginが複数登録された他の環境での優先順位は、この制御結果の対象外である。

現行方式の再観測では、対象worktreeのcommit OIDと未commit差分、agent-toolkitとagent-basicsのmanifestにあるversion、3実装sourceのSHA256を起動前に記録する。
後述の3 SHAと実装が一致することを確認する。
後続自然観測時の3 SHAは上記の値であり、再観測で選ぶ現行版とは異なる。
自然入力で取得を案内される資料は実行時に決まる。
起動前にagent-toolkitとagent-basics配下の資料ファイルの絶対パスとSHA256を記録し、実際にReadした各資料について、同じ絶対パスの事前SHAと取得時のファイルSHAを比較する。
Read本文は取得範囲を同じファイルの対応箇所と比較する。
事前記録に無い資料は版の一致を確認できない。
全文取得方式の代表自然観測と後続自然観測で対象となった資料の例を次に示す。
事前に記録する集合はこの3資料に限らず、2 plugin配下を対象とする。

- `agent-basics/skills/bugfix/references/root-cause-analysis.md`
- `agent-basics:bugfix/references/root-cause-analysis.md`
- `agent-basics/skills/writing-standards/references/python.md`

次は対象worktreeを`/home/aki/dotfiles/.claude/worktrees/process-wi-lane-05`とする例である。
Pythonの可変デフォルト引数の不具合を調査し、原因分析節を持つ短い報告書を新規作成するよう依頼する。
出力先は取得前に存在しない作業用パスを使う。
`<入力>`は依頼本文に置き換える。

```sh
timeout 180 claude -p --no-session-persistence --plugin-dir /home/aki/dotfiles/.claude/worktrees/process-wi-lane-05/agent-toolkit --plugin-dir /home/aki/dotfiles/.claude/worktrees/process-wi-lane-05/agent-basics --allowedTools Write,Read --output-format stream-json --verbose --include-hook-events '<入力>'
```

streamのplugin登録path・version、SkillのBase directory、Readのpath・本文・hashが対象worktreeと一致することを確認する。
初回Write入力、PreToolUseの全文取得・見直し案内、Write成功、SkillとRead、再Write・Editと各結果をイベントの順序で確認する。
PostToolUseのRead結果では本文の範囲・省略の有無と、同じ主体・資料hashに対応する取得済み状態を確認する。
未取得範囲、最終応答および終了状態も記録する。
公開イベントからモデル内部の利用を推論しない。

現行の再検証で選ぶ3 sourceのSHA256は次のとおりである。
公開後に各ファイルのSHAが一致する版を確認する。

| 現行実装source | SHA256 |
| --- | --- |
| `agent-toolkit/agent_toolkit/_hooks/pretooluse/read_delivery.py` | `541dfa9f284eed60737344a2c860ceab721b898fd287f77efd4c7578d9a81153` |
| `agent-toolkit/agent_toolkit/_common/read_triggers.py` | `e89e28d67bde492d15516738cc347b174dd57440450627ab19cbd23a84cd0969` |
| `agent-toolkit/agent_toolkit/_hooks/rules_context.py` | `50361034a30c66546ad75331ec5b9667923147d634f7f55fee22678f6bedb72c` |
