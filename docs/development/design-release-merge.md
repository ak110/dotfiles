# rulesetとマージ後の同期の設計記録

[設計記録の索引](design.md)と[branch・CI所有権・公開・終了の設計記録](design-release.md)から参照する主題別の記録である。

## developとmasterのbranch・リリース設計

### rulesetの更新手順

ruleset `21524717`のrequired checkは共通8名と`statusline-version`の9件とし、`statusline-version`以外は共通CIのjob表示名と一致させる。
ruleset更新前の個別GETでは、応答の完全IDが`21524717`、`source`が`ak110/dotfiles`、`target`が`branch`であり、条件が`refs/heads/master`を対象とすることを確認する。
確認した完全IDは、送信前後の個別GETとPUTのURLパス`repos/ak110/dotfiles/rulesets/21524717`へ固定する。
ruleset更新の本文はmanaged-tempの中のJSONファイルへ保存し、送信前に保存したJSONファイルを読み戻す。
トップレベルキーが`name`、`target`、`enforcement`、`bypass_actors`、`conditions`、`rules`のいずれかであり、`id`を含まないこと、`refs/heads/master`条件、9件のrequired check名を検証する。
検証に成功した同じファイルを`gh api --method PUT --input <検証したJSONファイルの絶対パス> repos/ak110/dotfiles/rulesets/21524717`へ渡す。
擬似端末の標準入力を更新本文の搬送に使わない。
更新要求が失敗した場合は、追加のPUTを実行する前に対象rulesetを個別GETで再取得する。
再取得した現行状態が更新前状態と完全に一致し、送信するJSONファイルの内容が検証時から変化しておらず、失敗の原因が本文の搬送であって送信方法をファイル入力へ是正できることを確認できる場合だけ、同じ本文の再送を1回だけ許可する。
現行状態を取得できない場合、現行状態が更新前状態と一致しない場合、更新後の確認が期待値と一致しない場合は再送せず、更新前状態と現行状態を保持して続行できない理由を返して終了する。
更新本文を擬似端末の標準入力へ渡す案は、本文が欠落した場合に更新未成立と適用結果不確定を区別できないため採用しない。
全ての更新失敗を状態不明として一律に再送禁止とする案も、更新が成立していないことを再取得で確定できる場合まで工程を停止させるため採用しない。

ruleset一覧には個別ref条件が含まれないため、同名候補の完全なIDを取得した後に個別GETで対象repository、branch rulesetおよび`refs/heads/master`を確認する。
候補が0件の場合は作成し、1件の場合は完全IDへPUTする。
複数件、取得失敗または対象不一致の場合は設定を変更しない。
`required_linear_history`はマージコミット要件と両立しないため設定しない。

GitHubのruleset API仕様は、2026年8月26日時点の[Rulesets REST API](https://docs.github.com/en/rest/repos/rules?apiVersion=2026-03-10)を参照する。
workflowの`workflow_run`入力境界は同日時点の[workflow_runイベント仕様](https://docs.github.com/actions/using-workflows/events-that-trigger-workflows#workflow_run)を参照する。

### マージ後の同期と検収

PRマージ後は、`origin/master`をマージコミットの基準として保持し、`git rev-parse --short=7 origin/master`で人間可読の識別子を取得する。
その後に`origin/master:refs/heads/develop`を明示したrefspecで`origin/develop`をpushする。
ローカルbranchを`origin/develop`更新の操作元にしない。
マージ後のmaster pushと同期後のdevelop pushに対するCIは待機しない。
`master`へは`develop`からのリリースPRだけをマージし、マージ後は`develop`を`master`へ同期するため、マージコミットのツリーはマージ前に必須checkの成功を確認したPR headのツリーと同じになり、developへ載るのも同じマージコミットである。
両pushのCIは同じ中身の再実行であり、待機してもリリースの完了時刻が後ろへずれる以外の効果が無い。
以前は「commitが同一」「ツリーが同一」などの省略条件を個別に判定していたが、マージコミットをdevelopへ同期した直後にdevelop側の条件が成立せず、同じ中身のCIを待つ事象が起きたため撤去した。
必要なRelease statuslineのrun・タグ・GitHub Release・2成果物、origin/developとorigin/masterの最終commitが一致することの確認は省略しない。
Release runはmaster CIの成功を契機に起動するため、statuslineの変更を含む場合のmaster CIの結論はRelease runの検収で確かめる。
同期push後に`git fetch origin develop master`する。
`git rev-parse --short=7 origin/develop`と`git rev-parse --short=7 origin/master`を個別に実行し、各出力の一意な短縮OIDを比較して、マージコミットとdevelopへ同期したコミットの一致を確認する。
ローカル`develop`を同期した場合は、`git rev-parse --short=7 develop`も同じ一意な短縮OIDであることを確認する。

`origin/master`の第一親との差分にstatuslineが含まれる場合は、同じcommitの`Release statusLine` run、タグ、GitHub ReleaseおよびLinux・Windows assetを検収する。
`gh run list --commit`が完全なSHAを要求するため、この呼び出しの直前に限って`origin/master`を完全OIDへ解決し、永続化しない。
statuslineの差分がない場合はRelease成果物を検収しない。
成功時は`origin/develop`と`origin/master`がマージコミットと同じcommitであることを7文字以上の一意な短縮OIDで確認する。
ローカル`develop`の同期は同期を実行する直前に作業ツリーのclean、現在branchが`develop`であること、マージコミットへのfast-forward可能性を再取得し、すべて成立した場合だけ実施して、リリースの成立条件から分離する。
現在branchを更新するコマンドを事前判定の結果だけで実行しない。
同期を実施した場合はローカル`develop`の参照を更新し、条件が成立せず同期を省略した場合は本手順がローカルの作業ツリーとローカルbranchへ書き込まない。
いずれの場合も、待機中に生じた変更を含めてローカルの状態をリリースの成否判定に用いない。
ローカルの同期状態をリリースの成立条件へ含める案は、公開対象がrefで固定された後も無関係な作業中の差分でリリースを停止させるため採用しない。

PRマージ、branch同期およびRelease検収の詳細は、プロジェクトスキル[merge-pr](../../.claude/skills/merge-pr/SKILL.md)を実行時の手順とする。

### 初回のbranch初期化

初回branch初期化は1回だけ実行する。
実装済みHEADのrefからローカル`develop`を作成して公開し、developのCIを確認する。
`origin/master`の移行前OIDを保存し、初回リリースPRを作成しない。
ローカル`develop`と`origin/develop`のOID一致を確認した後、ローカル`master`の削除直前OIDを記録して削除する。
公開またはCIが失敗した場合はローカル`master`を削除せず、成立済みの外部状態と再開点を報告する。
