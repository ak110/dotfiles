---
name: single-lane-process
description: >
  対象リポジトリのAWIを、レーンへ分けずメインが主作業ツリーでまとめて実装して終端するときに起動する。
disable-model-invocation: true
---

# AWIの単一レーン処理

メインがAWIを取得し、計画、実装、実行レビュー、終端を同じ処理回で行う。選定と実装を委譲して並列化する場合は`../process-wi/SKILL.md`を使う。本スキルの実行中は自律モードとし、WIの共通契約は`../wi-standards/SKILL.md`が定める。
WI作成、計画、実行および実行レビューの責務と受渡しは`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。

## process-wi契約の読み替え

`agent-toolkit:process-wi`を名指しする条文は、picker、レーン担当、終端担当または専用worktreeに依存する場合を除き、本スキルの実行中にも適用する。これらの役割・資源へ依存する契約は直接適用せず、本節が明示する同等の契約だけをメインの工程として適用する。

直接適用しないスキル側の文書は`agent-toolkit/skills/process-wi/`配下とする。
pickerの文書は`${CLAUDE_PLUGIN_ROOT}/share/pick-wi.parent.md`とする。
レーン実行の文書は`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`と`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`とする。
終端担当の文書は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md`および`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`とする。

本スキルから`agent-toolkit:plan-mode`を起動する場合は、確認事項をUWIへ登録する専用処理として扱い、計画の起草後は本スキルの実行順へ戻って主作業ツリーで実装する。計画stemは`dd-HHmm_single-lane-process`とする。実行レビューのレビューイーはメインとする。Codexでは`../plan-mode/references/codex-runtime.md`が専用処理へ定めるUWI記録と暫定判断を、本スキルにも適用する。

本スキルでは、レーン担当と終端担当を使わないメインが、対象リポジトリの版数規範の単一worktree向け一般則に従い、実装段階で版数を更新する。

## 処理の振り分け

処理対象の計画ファイル作成要否は`agent-toolkit:plan-mode`「計画ファイルの作成要否」で判定する。作成する項目を計画対象、省く項目を直接実装対象とする。

計画を要する項目は、対象worktreeごとの部分集合に分け、同じ対象worktreeの項目だけを1つの計画ファイルへまとめる。メインが各対象worktreeで`agent-toolkit:plan-mode`の計画起草手順に従って作成し、レビューは実装後の実行レビューだけで行う。単一worktreeだけの処理回では計画ファイルを1つに保つ。専用worktreeを作成せず、対応表が示すworktreeで実装する。

## 不変条件

- 作業ツリーへ書き込む主体はメインだけとする
- 実行レビューは、実装を担当しない独立した担当が行う
- 人間由来の不採用範囲は、ユーザーの確認を得てから終端する
- 手順で固定した集合は、処理回の終わりまでそのまま使う。`## 見つけた既存不良の扱い`で投入したAWIだけは例外として加える
- 同じ対象worktreeの計画項目と直接実装項目は1件の実行レビューへまとめ、異なる対象worktreeの項目は同じ計画または実行レビューへ混在させない

## 見つけた既存不良の扱い

`agent-toolkit:process-wi`の`## 即時対応`はレーンと是正レーンを前提とするため直接適用せず、本節の手順をメインの工程として適用する。元の作業と文脈を分けるのは調査だけとし、修正はメインが行う。

- 公開（実行順8）の開始前に見つけた既存不良のうち、`agent-toolkit:bugfix`の初動判定で拡張原因分析、類似見直しまたは再発防止策を要するものは、メインが不良ごとに調査、AWIの起草および投入を委譲する。委譲は`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`に従う。投入されたAWIは実行順1と同じく`atk wi start-processing <ファイル名> --target-repo=<対応付けたtarget_repo>`で`processing`へ移してから固定集合と対応表へ加え、自ら実装し、是正分はそのworktreeの実行レビューへ含める。追加の是正で見つかった不良も同じ手順で扱う
- 拡張原因分析を要さず1箇所で是正が完結する軽微な不良は、`agent-toolkit:bugfix`の`references/response.md`に従ってメインがその場で直す
- 公開の開始後に見つけた不良のうち、公開を妨げるもの（CI失敗など）は公開の手順の中で直す。公開を妨げないものは、同じ委譲で不良ごとにAWIを`source: process-wi`で投入し、次の処理回へ回す

## 実行順

1. processable一覧と各WI本文を取得する。同じ時点で`atk wi list --type=uwi --answered=yes --status=processable --target-repo=<対象リポジトリの絶対パス>`を実行し、回答済みUWIを取得する。取得した回答済みUWIは、実装へ着手する前に`agent-toolkit:wi-standards`「状態と依存」の回答済みUWIの取り込みに従って終端するか固定集合へ加える。残る全項目を直接実装または計画へ分ける。処理対象に依存が未達の項目が含まれる場合は、依存元を同じ処理回の集合へ加えるかを「確認を要する事項」としてユーザー確認へ回す。確認を経ずに依存元を加えることと、依存未達の項目を集合から無断で外すことのいずれも選ばない。各WIについて、WIのファイル名、保存済みの`target_repo`、Git操作に使うworktreeの絶対パスおよびそのworktreeで解決した処理開始時のHEADの7文字以上の一意な短縮OIDを対応付ける。対象を`atk wi start-processing <ファイル名>... --target-repo=<対応付けたtarget_repo>`で`processing`へ移し、対応表と集合を固定する。
2. 以降の`atk wi`操作は対応表の`target_repo`を使い、Gitの起点比較、実装、検証、commitおよびレビューは対応表のworktreeと処理開始OIDを使う。別のworktreeまたは複製元のHEADを代用しない。
3. 計画対象がある場合は`agent-toolkit:plan-mode`のSKILL.mdと計画ファイル基準を全文読み、対象worktreeごとの部分集合を各1つの計画ファイルへ起草する。計画メタ情報の対象リポジトリと計画構造の自動チェックの`--work-dir`にはその部分集合のworktreeを使う。作成と計画構造の自動チェックは同基準が定める手順で行う。
4. 対応表が示すworktreeで、計画対象は`## 要件・外部仕様`、直接実装対象はWIの要求と完成条件に従って実装する。計画対象は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`の受入シナリオ検証で検証し、シナリオ別のテスト名と合否を記録する。直接実装対象もWIの利用者と呼び出し手段から同じテストを選ぶ。`agent-toolkit:commit`に従ってcommitする。互いに依存しない対象worktreeの部分集合は並行してよいが、各worktreeへ書き込む主体はメイン1つのまま保つ。
5. 対象worktreeごとに1件の実行レビューを`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って起動する。そのworktreeに計画対象がある場合は対応する計画ファイルの絶対パスを渡し、直接実装対象がある場合は対応するWIの記録を渡す。両方がある場合は同じ起動文へ渡す。計画対象が無い場合は、そのworktreeで解決した処理開始OIDを渡す。レビュー起動時の`cwd`、処理開始OID、レビュー表およびレビュー基準には同じ部分集合の値だけを使う。
6. 対象worktreeごとに`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`に従って指摘を収束させる。修正はメインが行い、同じ起点OIDとレビュー表を継続する。
7. 各計画について`atk run-script plan-progress --`で完了判定を`## 進捗ログ`へ記録し、計画構造の自動チェックの成功を確認してから計画バンドルを保存する。
8. 各WIを採否に応じて`adopt`または`reject`し、対象worktreeごとに`agent-toolkit:commit`をSkill機能で起動し、同スキルの`references/publish.md`を全文読んで公開する。`adopt`では`agent-toolkit:wi-standards`「状態と依存」のcommit対応付けを使う。複数の対象リポジトリでは成果依存を保ち、独立した対象のpushを先に全件終えてからCI監視を並行開始する。対象リポジトリ、ref、baselineおよび監視識別子を対応付け、全識別子の終端を待って結果を個別に回収する。CI成功を入力にする固有工程は、その対象の成功後に行う。待機中は結果を入力とせず同じ書込資源を占有しない固有工程を進めてよい。
   開発マシン上の常時稼働サーバーへの反映は、`agent-toolkit:completion-report`「工程」手順1が定める反映手段と稼働確認手段を持つ対象で、実装・レビューが収束したHEADを使える場合にCI待機と並行して始める。反映が対象リポジトリへ書き込まず、公開の入力を生成せず、読み取る成果物と排他資源が公開操作と競合しないことを確かめる。反映したHEADの完全OID、反映コマンドの終了状態と稼働確認の結果を保持して完了報告へ渡す。後続のcommitによる差分は完了報告の同手順で判定する。
   Claude Codeでは、全ての未終端識別子を条件とする1つの`Monitor`のuntil-loopで終端を待つ。固定時間の`sleep`と状態変化の無い空の`ReadNotifications`の反復は待機手順へ加えない。
9. 一時的なレビュー表を正式な保存または回収契約に従って処理し、`agent-toolkit:completion-report`で報告する。
