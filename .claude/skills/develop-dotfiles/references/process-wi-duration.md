# dotfilesのprocess-wiの所要時間と完了条件

`agent-toolkit:atk-mode`の作業`process-wi`の所要時間目標を、本リポジトリを対象に含む巡回へ当てはめる値と条件である。
進捗と時間目標の見直しは原則2（`agent-basics:writing-standards/references/quality-principles-qcd.md`）に従う。

## 所要時間と完了条件

本リポジトリを対象に含む巡回は、WIごとに選定（取り込み）から、そのWIを含む公開の反映検収までを原則180分以内に収める。
別cwdのall-repos統括にも適用する。
選定規則は`agent-toolkit:atk-mode/playbooks/process-wi-selection-allocation.md`「調査とレーン分け」に従う。
実測による補正は`agent-toolkit:atk-mode/references/process-wi-selection-estimates.md`「見込みの補正の算出」に従う。

各WIの実装・必要な品質確認と、そのWIを含む公開のCI・反映を検収した時点を計測の終点とする。
選定と統合は公開待ちでも続け、前回報告以降に届いた成果をリリースごとの報告用UWI1件へまとめる。
常駐の統括の終了は`agent-toolkit:atk-mode/playbooks/process-loop.md`「中断と再開」の条件に限る。
着手後は選んだ全件と必要な品質工程を保持し、同じ巡回で完遂する。
目標を超えた回は、原因と改善をAWIとして登録して次の巡回で処理する。
各WIの取り込み時の見込みと反映検収までの実測をリリース時の報告で比べ、次回の配分と工程の改善に使う。
次回の目標達成に必要な新たな改善の実装・公開・見込み検収は、次回の選定と実行で行う。
選定確定後の超過は警告にとどめ、選定を受理して配分を比較する。

レーン数の上限は取り込み契機ごとにCLIの未終了WIベースの既存の式で増減する。
独立準備・先行レビュー・共有検証と成功再利用により、選んだ仕事を効率よく処理する。
同じホストで把握している方式・性能の全量比較も共有実行枠を占める有限資源として配分に含め、自分の巡回が起動する計測は必須検証と重ならない時間へ置く（`verification-execution.md`「検証コマンドの実行」）。
別セッションが起動元の計測は制御できるとみなさず、その計測の担当と合意した空き時間で行う。
内部担当の読み取り・実装・レビューを合わせた同時数Cは資源・サービス・総費用から選ぶ。
マシン全体で作業中の委譲先の数は、役割を問わずagents_serverの上限（`atk config`の`agents_concurrency`。初期値は論理CPU数×2.5）で起動時に制限されるため、レーン担当と評価用担当を含めた合計がこの上限を超えるCを見込みに使わない。
進行中の30分点検では、残量・実測速度・待ち・依存を比較し、上限内で構成を見直す。
計測範囲は`.chezmoi-source/dot_claude/docs/session-review-dotfiles.md`「セッションの所要時間目標」に従う。

## 所要分析の基準値

振り返りで巡回の所要を比較する時は、`docs/development/design-session-review-materials.md`「所要分析の基準値」を使う。
