# バージョン更新の詳細手順

`.claude/skills/agent-toolkit-edit/SKILL.md`「バージョン更新」節の詳細手順を集約する。
本節のバージョン更新規定は`agent-toolkit/`配下（agent-toolkitプラグイン配布物）のみを対象とする。
`.chezmoi-source/`配下のchezmoi配布物・`bin/`配下のCLIラッパー・`scripts/`配下のヘルパースクリプトは
本規定の対象外とし、`agent_toolkit_bump.py`も更新しない。

## 判定基準

エンドユーザーに届く振る舞いが変わるものは必ずbumpし、判断に迷う場合はbumpする
（pre-1.0であれば頻繁にMINORを更新しても問題ない）。
`git commit`時に`agent-toolkit/`配下の変更を含みつつ`plugin.json`の`version`未変更の場合、
検知フックが`warn`を返す。bump不要に該当する場合は警告を無視して進める。
コメント・docstringのみ、`*_test.py`のみ、入出力が不変なリファクタリング、誤字・スタイル調整はbumpしない。

- PATCH（`+0.0.1`）: 軽微な修正（フック・検出パターン・メッセージの変更、バグ修正、
  既存の見出し配下への規範文追記・条件補強・例示追加など）
- MINOR（`+0.1.0`）: 機能追加・検出範囲の大幅拡大・description変更・節新設など、規模の大きい変更
- MAJOR（`+1.0.0`）: ユーザーからの明示的な指示がない限り行わない
- 現行版が`major.minor.patch`の数値3要素で表せない非SemVerの場合は、文字列の辞書順・
桁数・接尾辞からPATCH/MINOR/MAJORを推測しない。プロジェクト固有の対応表又はユーザーの
  明示指定がある場合だけその区分を適用し、どちらも無い場合はbumpを行わず判定不能として報告する

## 競合解決と統合後の確認

rebase・merge時に`version`が競合した場合は、`(major, minor, patch)`の数値タプルが大きい方を採用する。
採用値を正本2ファイル（`agent-toolkit/.claude-plugin/plugin.json`・`.claude-plugin/marketplace.json`）へ反映する。
反映後に`scripts/sync_codex_plugin_manifests.py`でAgent Plugins・Codex向け派生manifestを同期し、
`scripts/sync_codex_plugin_manifests.py --check`で派生物を変更せず整合性を検査する。
検査は最新なら終了コード0、不整合なら終了コード1、引数誤用なら終了コード2とする。
4ファイルの`version`と`description`が一致することを確認する。

rebaseまたはmerge後はGit競合の有無にかかわらず、公開済みの統合先と現在の正本の`version`値を比較する。
自分の未公開コミットにエンドユーザーの振る舞いを変えるplugin変更が残り、両者の値が同じ場合は、
統合後の版を基準として「判定基準」節に従うbumpを再実行する
（別の作業が同じ版へ先にbumpし、Gitが非競合として統合した場合の配信漏れを防ぐ）。

## 未プッシュ範囲での統合

`scripts/agent_toolkit_bump.py`は既存bump以下の種別指定をno-op扱いとするため、
未プッシュ範囲では後続編集ごとに追加bumpせず、格上げが必要な場合だけ上位種別を指定する。
`git push`実行後の追加commitは新たな未プッシュ範囲として扱い、エンドユーザー振る舞い変更を含む場合は再度bumpする。

## Codex導入後のroot再照合

Codexプラグインの導入版が変わった場合、保持済みのplugin rootを再利用せず再解決する。
`codex plugin list --json`の`pluginId == "agent-toolkit@ak110-dotfiles"`項目から現行の導入版を取得し、
plugin cache directory配下の新versionのrootを解決し直す。
再解決したroot配下でplugin manifestの`version`が導入版と一致することと、
利用する後続スクリプトの実在を確認してから使う。
確認できない場合は旧rootへフォールバックせず未完了として扱う
（`source.path`は配布元を示す値であり、導入cache rootの更新には使用しない）。

## plan modeでの取り扱い

計画フェーズではbump要否や既存bumpとの差分を調査せず、種別（PATCH／MINOR／MAJOR）と
「判定基準」節に基づく種別選定根拠を実装者向け領域へ記述する。
具体的なversion数値は書かず`scripts/agent_toolkit_bump.py`の実行結果に従う。
実装フェーズでは検証より前に`scripts/agent_toolkit_bump.py {種別}`を実行する
（既存bumpとの統合はツール側が吸収する）。bump不要の場合は実装者向け領域へ`bump不要`と根拠を記載する。
version bumpを伴う計画では、Claude Code向け正本2ファイルを実装資料の変更説明へ含める。
正式な生成コマンドと生成器出力との一致確認は実装者向け領域へ記載する。
生成コマンドが扱う派生manifestは、version・description欄の有無や実際の差分有無を問わず変更説明へ重複して含めない。
派生manifestの完全性は生成コマンドの実行と生成器出力との一致確認で保証する。
Agent Plugins・Codex向けmanifestは`agent_toolkit_bump.py`の直接更新対象ではなく、
正本更新後に`scripts/sync_codex_plugin_manifests.py`で反映し、同スクリプトの`--check`で非変更検査する。
bumpの完了条件は、実装開始時点の版との増加比較で判定しない。
公開済み基準（`git push`済みの最新版のplugin manifest）に対して要求種別以上のbumpが含まれること、及び正本2ファイルと派生manifestの`version`が一致することで判定する。
既存の未プッシュbumpが要求種別以上であり`scripts/agent_toolkit_bump.py`が無変更で終了コード0を返す場合は、完了条件を満たす正常結果として扱う。
複数レーンを並列実装するフィードバック処理では、各レーンはbump種別（`bump不要`を含む）と選定根拠、MAJORの場合は認可根拠を計画へ記録するに留める。
メインが本規定の適用対象となるレーンの記録から最も上位の種別を確定し、全レーンのマージ後に1回だけ実行する。
統合ブランチのpush前に上流進行を観測してrebaseした場合は、`agent-toolkit:process-feedbacks`の
`references/plan-impl-feedback-flow.md`「レーン自己マージによる逐次統合」手順12に従う。
rebase後の公開済み統合先と正本のversionを「競合解決と統合後の確認」節の基準で再比較する。
未公開の振る舞い変更が公開済みと同じversionのまま残る場合は、メインが再bumpとmanifest同期を行う。

## 新規CLI公開時の疎通経路確認

配布物プラグインで新規CLI・新規コマンド・新規ラッパースクリプトを公開する変更を対象とする計画では、
計画段階の実装資料にエンドユーザー環境での疎通経路を含める。
疎通経路は、インストールスクリプト・post-apply処理・PATH配置手法・bash補完登録・Windowsペアファイル同期を対象とする。
判断基準は、プラグイン単体のエンドユーザーがPATH追加・環境変数の設定以外の追加設定なしで新CLIを起動できるかとする。
プラグインの`bin/`配下CLIの実配置先はバージョン付きcache directoryとなる。
このため、dotfiles配布のエンドユーザーは`.bashrc`のPATH追加、プラグイン単体のエンドユーザーは
`install-claude.sh`/`install-claude.ps1`が配置する動的解決ラッパーで吸収する。
