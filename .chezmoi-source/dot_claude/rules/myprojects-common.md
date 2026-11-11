<atk-auto source="dotfiles" kind="rules" path=".chezmoi-source/dot_claude/rules/myprojects-common.md">
# myprojects-common.md: ホスト共通の個人プロジェクト規範

実行ホストとエージェントの種別によらず、同一作者の個人プロジェクト全体へ適用する規範を置く。
ホストごとに異なるプロジェクト一覧と同期方針は、Claude Codeへ配布する`myprojects.md`が扱う。

## AWI処理の開始時の横断同期

個人プロジェクトの`agent-toolkit:process-wi`で選定を担当するメインは、選定開始より前に`operate-ak110-projects`スキルを起動する。
判定済みの条件を受けて依存更新だけを実施するレーンは、この契機では起動しない。
同期調査と依存更新の要否の判定結果は、そのセッションの後続の工程へ渡す。
起動名はClaude Code・Codexとも`operate-ak110-projects`とする。
Claude Codeでは`Skill`ツールから起動する。

## WI作成時の追従判定

個人プロジェクトの性質や作業手順（開発・検証できる環境、セットアップ手順、リリース運用など）を変える要求のWIを作成するときは、反映先を確定する前に`operate-ak110-projects`スキルを起動する。
同スキル「判定手順」に従い、そのプロジェクトを記述するdotfiles側の規範への追従と反映先を確定する。

## 個人プロジェクトのリリース開始手順

個人プロジェクトの協調モードの変更では、公開範囲を判定する前に`operate-ak110-projects`を起動し、「リリース運用」の恒常的な認可と明示指定の優先を適用する。

個人プロジェクトでpatch、minorまたはmajorのリリースを求められたときは、具体的な公開コマンドを選ぶ前に
`operate-ak110-projects`スキルを起動し、同スキルの「リリース運用」に従う。
個人プロジェクトの一連の作業（`agent-toolkit:process-wi`の公開工程を含む）がpushとCI成功まで終わったときも同スキルを起動し、「リリース運用」に従ってリリース要否を判定する。
agent-toolkit自身のversion bump、個人プロジェクト外、Dockerイメージの再構築およびworkflow内部の処理は対象外とする。

## pytilpackに無い汎用処理の改善要望

`agent-basics:writing-standards`の`references/pytilpack.md`「汎用処理を自作する前の確認」で、必要な汎用処理がpytilpackに無かった場合に適用する。
作業対象が個人プロジェクトの一覧に無いプロジェクト（業務のプロジェクトなど）でも適用する。
ユーザーへ確認せずに、pytilpackのリポジトリを対象とするAWIを`agent-toolkit:wi-standards`の通常の投入手順で投入し、投入したAWIを作業完了報告で伝える。
その場の作業は、同資料のとおりプロジェクト内へ実装して続ける。
AWIには汎用化した要求（必要な機能、想定する入出力、利用場面の一般的な説明）だけを書き、作業中のプロジェクトに固有の名称、コード、データは除く。
pytilpackは公開リポジトリであり、要求の内容が実装とcommitとして公開されるためである。
pytilpackのキューへ投入できない環境では、作業完了報告だけを行う。

## 検証付き実装委譲のlow開始の試行

個人プロジェクトに限り、検証コマンドで合否が決まる実装の委譲を低いeffortで始め、検証に失敗した分だけeffortを上げる運用を試している。
効果は後続の評価AWIが集計して確かめ、継続か撤去かを決める。
それまでは試行であり、工程別設定の値（`atk config`の設定、プリセット、初期値）は変えない。

対象は、`chores`工程へ渡す委譲のうち、内容が確定した実装で、合否が検証コマンド（テスト、lint、ビルドなど）の終了状態で決まるものとする。
次の委譲は対象外とし、設定どおりに起動する。

- `atk commit`のメッセージ生成、読取・要約・走査、`refine-prompt`など、合否を自動判定できない用途
- `chores`以外の工程
- ユーザーがWIの`担当モデル`などでモデルやeffortを明示した委譲
- `model_type`へ候補列を直接渡して上書きする委譲

対象の委譲は、委譲する日のローカル日付の「日」で起動形を決める。

- 奇数の日: `atk config get chores_model`の各候補のeffortを`low`へ置き換えた候補列を、`model_type`へ渡して起動する。
  例: `codex:sol/medium,claude:sonnet[1m]/medium`は`codex:sol/low,claude:sonnet[1m]/low`とする。
- 偶数の日: 設定どおり`model_type: chores`で起動する。

検証が失敗した場合は、`agent-toolkit:delegation`の`references/model-and-effort.md`が定める引き上げ順に従い、effortを1段上げて再委譲する。
再委譲の入力には、失敗した検証の出力と残る差分を含める。

評価で2つの群を機械的に取り出せるよう、対象の委譲（両方の群と再委譲）へ次の目印を付ける。

依頼本文の最初の行を次の形にする。

```text
試行標識: chores-low-trial group=<low|configured> attempt=<連番> retry-of=<session_idまたは->
```

- `group`は奇数の日の起動で`low`、偶数の日の起動で`configured`とし、再委譲は元の委譲の値を引き継ぐ。
- `attempt`は1から数え、`retry-of`は初回で`-`、再委譲で直前の委譲の`session_id`とする。
- 委譲先へ、返却の末尾に`変更規模: <git diff --shortstat の出力>`の行を置くよう求める。
- 検証の合否は、その委譲を`retry-of`で指す再委譲の有無で表す。
  再委譲があれば不合格、無ければ合格である。

評価では、委譲元のセッション記録から目印の付いた起動を次のコマンドで取り出す。

```text
atk run-script session-review-evidence -- --transcript <記録の絶対パス> --tool-calls --tool <startのツール名> --input-regex 'chores-low-trial'
```

effortとトークンの内訳は、同じコマンドの`--stats`が返す担当記録から取り出す。
</atk-auto>
