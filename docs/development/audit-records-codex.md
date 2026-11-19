# 監査記録: Codexの入出力とプラグイン

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年10月19日

2026年10月19日、Codex CLI 0.163.0で、戻り値の一部だけを表示すると識別子を失い、待機を重ねた。
戻り値全体を表示した対照では、`write_stdin`で1つの待機を回収した。
今回の文書追随では保存済みの観測記録を比較しており、実機では再現していない。
再検証では、長時間コマンドのyield結果が`session_id`を持ち`exit_code`を持たないこと、`text(r)`で両欄と本文が保持されること、同じ識別子で終了を回収できることを比べる。
一次記録はリポジトリ外の作業記録にある。

## agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年10月22日

WI原本の2026年10月22日の観測（Codex CLI 0.163.0）では、起動直後の`ALL_TOOLS`へstdio MCPがまだ登録されず、20秒後の別の`exec`ではagents_serverの6ツールとpyfltrが現れた。
2026年10月20日の比較記録では`tools.mcp__agents_server__start`の呼び出しが成功している。
本変更の担当も2026年10月22日の`functions.exec`で`ALL_TOOLS`から同じ6ツールの名前と公開説明を取得した。

再検証は同じ設定のCodex CLIで、起動直後と20秒後の別の`exec`から`text(ALL_TOOLS.map(x => x.name).filter(n => n.startsWith("mcp__")))`を実行する。
引数は該当要素の`description`のexec tool declarationから読み、同じ`exec`から`tools.mcp__agents_server__list({})`を呼ぶ。
原稿担当は配布先の変更を認可されていないため、生成物配布後の通常委譲の試行は統合担当が行う。

## agent-toolkit/skills/delegation/references/codex-runtime-waiting.md：agents_serverの二層待機：2026年11月15日

- 観測日: 2026年11月15日
- 確認版: Codex CLI 0.169.0。
  有限210秒の検体で空入力write_stdinの180000ms指定が180.001秒待ち、同じsessionを後続で終了7まで回収した。
  外側pragmaとfunctions.waitも180000msを受理した。
  190秒で終了0の検体は、終了済みなら指定時間まで待たず回収できた。
  最大値やコマンド自身の期限の測定ではない
- 再検証: 専用の有限190秒・210秒の検体を1回ずつ起動する。
  初回exec_commandを30000ms以内、以降の外側pragma・functions.wait・空入力write_stdinを180000msとして同じ識別子で回収する。
  標準出力・標準エラーと終了0・非0を区別する。
  同じ190秒検体の直接回収とshell担当では、親の起動・受領応答と子全応答のtoken_countを対応付ける。
  総入力・非cached入力・出力、全応答回数と起動から受領までの所要を比較する。
  今回の総入力は1346807対1053569token、非cached入力は1911対41857だった。
  出力は785対2033、全応答は3対8だった。
  親が独立作業中に遅れた受領時間は方式の性能差へ帰属させず、この190秒条件でshellの総費用優位は未確認とした
