"""`atk`のコマンド木へサブコマンドを登録する唯一の経路を提供する。

親の一覧へ表示する要約と、当該コマンド自身の`--help`へ表示する説明の双方を必須入力とする。
argparseの既定では前者だけを設定でき、各コマンドの`--help`が自分の目的、対象、前提、後始末を
示さない状態を許すため、登録経路の側で双方を要求する。
定型の見出しと組み込みヘルプの説明も日本語へそろえ、折り返しでは識別子を分割しない。
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import textwrap
from typing import Any

ROOT_DESCRIPTION = "目的: agent-toolkitのフィードバックキュー、計画ファイル、レビュー指摘管理表、管理対象一時領域と委譲支援を1つのコマンドから操作する。\n利用場面: ユーザーとコーディングエージェントが、フィードバックの投入から計画、実装、保存までの一連の作業を進めるとき。\n対象と出力: サブコマンドを指定しない場合はコマンド一覧を標準出力へ書き、何も変更しない。実際の読み書きは各サブコマンドが行う。\n前提: private-notesを扱うサブコマンドは`atk config get private_notes`が返すリポジトリを使う。\n復元・後始末: 本コマンド自身は状態を残さない。各サブコマンドの後始末は当該コマンドの`--help`に示す。"
ROOT_EPILOG = "各コマンドの詳細は`atk <コマンド> --help`で表示する。階層コマンドではさらに`atk <コマンド> <サブコマンド> --help`を使う。\n\n実行例:\n\n  atk mq list\n  atk config show"

HELP: dict[str, dict[str, str]] = {
    "atk mq": {
        "summary": "フィードバックとTBDのキューを操作する",
        "description": "目的: 対象リポジトリごとのフィードバックとTBDを、投入、参照、状態遷移、編集、常駐処理の各サブコマンドで扱う。\n利用場面: ユーザーが改善要求を投入するとき。コーディングエージェントが未処理のキュー項目を確認して処理するとき。\n対象と出力: private-notesのinboxとprocessing配下のファイルを読み書きする。サブコマンドを指定しない場合はサブコマンド一覧を標準出力へ書き、何も変更しない。\n前提: `atk config get private_notes`が返すリポジトリが存在すること。対象リポジトリはカレントディレクトリのGit remoteから決まる。\n復元・後始末: ファイルを変更するサブコマンドは変更をcommitする。未コミットの変更が残る場合は`atk mq commit`で確定する。",
        "epilog": "実行例:\n\n  atk mq list\n  atk mq show 20260901-072734-001",
    },
    "atk mq add": {
        "summary": "エントリをinboxへ投入する",
        "description": "目的: フィードバック又はTBDをinboxへ1件以上投入し、保存本文を標準出力へ表示する。\n利用場面: 改善要求、不具合、確認事項を後続のセッションへ引き継ぐとき。\n対象と出力: private-notesリポジトリのinboxへファイルを追加してcommitとpushを行う。書き込み前に確定した本文と保存結果から読み直した本文の一致判定、及び保存本文を標準出力へ書くため、送信元本文との照合に追加の`atk mq show`を要しない。\n前提: 本文をMESSAGE、`--body-file`、$EDITORのいずれかで与える。対象リポジトリは省略時にカレントworktreeから解決する。\n復元・後始末: 投入した項目は`atk mq rm`で削除でき、削除後もprivate-notesのGit履歴から復元できる。",
        "epilog": '実行例:\n\n  atk mq add "認証エラーの再現手順を整理する"',
    },
    "atk mq list": {
        "summary": "エントリを1件1行で一覧表示する",
        "description": "目的: 対象リポジトリと状態で限定したキュー項目を、ファイル名、target_repo、状態ラベル、本文冒頭の要約とともに列挙する。\n利用場面: 未処理の項目を把握するとき。処理対象の件数を確認するとき。\n対象と出力: private-notesを読み取り、標準出力へ1件1行で書く。エージェント環境ではJSON Lines、それ以外ではテキスト形式を既定とする。ファイルは変更しない。\n前提: 既定でremoteと同期する。同期を避ける場合は`--skip-pull`、必ず同期する場合は`--pull`を指定する。\n復元・後始末: 読み取りだけを行うため不要。",
        "epilog": "実行例:\n\n  atk mq list --status=processable",
    },
    "atk mq show": {
        "summary": "指定エントリまたは全件の本文を表示する",
        "description": "目的: ファイル名で指定した項目、又は対象範囲の全件の本文をfrontmatterとともに表示する。\n利用場面: 処理を始める前に要求の原文を確認するとき。複数件を1回の実行で取得するとき。\n対象と出力: private-notesを読み取り、標準出力へtarget_repoごとに区切って書く。ファイルは変更しない。\n前提: FILENAMEを1件以上指定するか`--all`を指定する。FILENAME指定時は全ての状態ディレクトリを探索する。\n復元・後始末: 読み取りだけを行うため不要。",
        "epilog": "実行例:\n\n  atk mq show 20260901-072734-001.md --target-repo=github.com/ak110/dotfiles --skip-pull",
    },
    "atk mq grep": {
        "summary": "本文を正規表現で検索して該当行を列挙する",
        "description": "目的: 対象範囲のキュー項目の本文全体をPythonの正規表現で検索し、ファイル名、行番号、該当行を列挙する。\n利用場面: 同じ主題の既存項目を探すとき。特定の識別子を含む項目を洗い出すとき。\n対象と出力: private-notesを読み取り、標準出力へ`<ファイル名>:<行番号>:<該当行>`の形式で書く。該当が0件のときは終了コード1を返す。ファイルは変更しない。\n前提: PATTERNはPythonのreモジュールが解釈できる正規表現として与える。\n復元・後始末: 読み取りだけを行うため不要。",
        "epilog": '実行例:\n\n  atk mq grep "worktree-stash" --status=all',
    },
    "atk mq start-planning": {
        "summary": "通常型フィードバックをplanningへ移す",
        "description": "目的: inboxの通常型フィードバックをplanningへ移し、計画作成中であることをキュー上へ表す。\n利用場面: 複数件を1つの計画へ取り込む前に、対象を計画作成中として占有するとき。\n対象と出力: private-notesのinboxからplanningへファイルを移動し、commitとpushを行う。\n前提: 対象がinboxにあり、通常型フィードバックであること。\n復元・後始末: `atk mq return-to-inbox --state=planning`でinboxへ戻す。",
        "epilog": "実行例:\n\n  atk mq start-planning 20260901-072734-001.md --target-repo=github.com/ak110/dotfiles",
    },
    "atk mq start-processing": {
        "summary": "フィードバックをprocessingへ移して処理中にする",
        "description": "目的: inboxのフィードバックをprocessingへ移し、処理中であることをキュー上へ表す。\n利用場面: 選定した対象の処理を開始するとき。複数件を1回の実行で指定する。\n対象と出力: private-notesのinboxからprocessingへファイルを移動し、commitとpushを行う。\n前提: 対象がinboxにあること。\n復元・後始末: `atk mq return-to-inbox`でinboxへ戻す。",
        "epilog": "実行例:\n\n  atk mq start-processing 20260901-072734-001.md --target-repo=github.com/ak110/dotfiles",
    },
    "atk mq hold": {
        "summary": "inboxまたはprocessingの項目を保留する",
        "description": "目的: inbox又はprocessingの項目をholdへ移し、自動処理の対象から外す。\n利用場面: 外部条件が整うまで当該項目を処理させないとき。\n対象と出力: private-notesの該当ディレクトリからholdへファイルを移動し、commitとpushを行う。\n前提: 対象がinbox又はprocessingにあること。\n復元・後始末: `atk mq unhold`でinboxへ戻す。holdは自動処理からの除外だけを意味し、編集、回答、採用、不採用、削除はinboxと同じ条件で行える。",
        "epilog": "実行例:\n\n  atk mq hold 20260901-072734-001.md",
    },
    "atk mq unhold": {
        "summary": "holdの項目をinboxへ戻す",
        "description": "目的: holdの項目をinboxへ戻し、自動処理の対象へ復帰させる。\n利用場面: 保留の理由が解消したとき。\n対象と出力: private-notesのholdからinboxへファイルを移動し、commitとpushを行う。\n前提: 対象がholdにあること。保留元がprocessingであった場合もinboxへ戻る。\n復元・後始末: 再び保留する場合は`atk mq hold`を使う。",
        "epilog": "実行例:\n\n  atk mq unhold 20260901-072734-001.md",
    },
    "atk mq return-to-inbox": {
        "summary": "processingまたはplanningの項目をinboxへ戻す",
        "description": "目的: processing又はplanningの項目をinboxへ戻し、未処理の状態へ復帰させる。\n利用場面: 処理を中断するとき。外部条件待ちで一定期間だけ再処理を避けるとき。\n対象と出力: private-notesの該当ディレクトリからinboxへファイルを移動し、commitとpushを行う。`--cooldown-days`を指定すると、指定した日数だけ再処理の対象から外す。\n前提: 対象がprocessingにあること。planningから戻す場合は`--state=planning`を指定する。\n復元・後始末: 処理を再開する場合は`atk mq start-processing`を使う。",
        "epilog": "実行例:\n\n  atk mq return-to-inbox 20260901-072734-001.md --cooldown-days=3",
    },
    "atk mq adopt": {
        "summary": "採用として終端し対応結果を記録する",
        "description": "目的: 対応済みの項目をadoptedへ移して終端し、採否の結果と対応commitを記録する。\n利用場面: 要求への対応を完了し、対象リポジトリへ反映したとき。\n対象と出力: private-notesのinbox又はprocessingからadoptedへファイルを移動する。`--note`の内容を本文末尾の`## 処理結果`節へ追記してcommitとpushを行う。\n前提: 対象がinboxかprocessingにあること。`--commit`で指定するrevisionは対象リポジトリで解決できること。\n復元・後始末: 終端した項目はキューの一覧に現れない。取り消す場合はprivate-notesのGit履歴から復元する。連続操作の中間では`--skip-push`でpushを省略し、最後の操作では指定しない。",
        "epilog": '実行例:\n\n  atk mq adopt 20260901-072734-001.md --note="計画で対応済み"',
    },
    "atk mq reject": {
        "summary": "不採用として終端し理由を記録する",
        "description": "目的: 対応しないと確定した項目をrejectedへ移して終端し、理由を記録する。\n利用場面: 要求を採用しないと判断したとき。\n対象と出力: private-notesのinbox又はprocessingからrejectedへファイルを移動する。`--note`の内容を本文末尾の`## 処理結果`節へ追記してcommitとpushを行う。\n前提: 対象がinboxかprocessingにあること。`--if-inbox`を指定した場合は、pullの後も全対象がinboxにあるときだけ終端する。\n復元・後始末: 終端した項目はキューの一覧に現れない。取り消す場合はprivate-notesのGit履歴から復元する。",
        "epilog": '実行例:\n\n  atk mq reject 20260901-072734-001.md --note="現行実装で解消済み"',
    },
    "atk mq rm": {
        "summary": "指定項目または未処理・処理中の項目を削除する",
        "description": "目的: 指定した項目、又は対象リポジトリの未処理と処理中の項目をまとめて削除する。\n利用場面: 自身の誤りで投入した項目を整理するとき。統合済みと移管済みの元項目を除去するとき。\n対象と出力: private-notesから対象ファイルを削除し、commitとpushを行う。`--all`では削除の前に対象を一覧表示する。\n前提: 個別削除ではFILENAMEを1件以上、一括削除では`--all`と`--target-repo`を指定する。planningとprocessingの項目は既定で保護し、削除するには`--force`を指定する。\n復元・後始末: 削除した内容はprivate-notesのGit履歴に残るため、必要な場合は当該commitから復元する。",
        "epilog": "実行例:\n\n  atk mq rm 20260901-072734-001.md",
    },
    "atk mq edit": {
        "summary": "エントリの本文とメタデータを編集する",
        "description": "目的: 既存項目の本文とメタデータを、非対話又は$EDITORで編集する。\n利用場面: 投入済みの要求へ情報を補うとき。記述の誤りを直すとき。\n対象と出力: private-notesの対象ファイルを書き換え、commitとpushを行う。書き込み前に確定した本文と保存結果から読み直した本文の一致判定、及び保存本文を標準出力へ書く。コーディングエージェントの実行環境から起動した場合は、`## ユーザーコメント`節を編集の対象から外し、保存済みの内容をそのまま残す。\n前提: FILENAMEを省略した場合は、inbox配下でファイル名順が最大の項目を$EDITORで開く。`--append`はTBDを対象にしない。コーディングエージェントの実行環境から起動した場合、MESSAGEへ`## ユーザーコメント`節を含めると編集を拒否する。\n復元・後始末: 編集前の内容はprivate-notesのGit履歴に残る。",
        "epilog": '実行例:\n\n  atk mq edit 20260901-072734-001.md "更新後の本文"',
    },
    "atk mq convert-to-plan": {
        "summary": "フィードバックを計画実装型へ変換する",
        "description": "目的: 既存フィードバックを計画実装型へ変換し、planning入力では全件を最古の1件へ統合する。\n利用場面: 計画ファイルの作成とレビューが収束し、実装へ引き渡すとき。\n対象と出力: private-notesの対象ファイルへ計画ファイルの参照と依存を記録する。planning入力では統合元を同じcommitで除去してinboxへ移す。1回のcommitと任意のpushで処理する。\n前提: `--plan-file`へ`$(atk config get private_notes)/plans/`から始まる可搬表記を指定する。入力の状態を混在させない。planning入力では`--message`を指定する。\n復元・後始末: commitの前に失敗した場合は部分的な変換を残さない。pushだけが失敗した場合はcleanなローカルcommitが残るため、pushから再開する。",
        "epilog": "実行例:\n\n  atk mq convert-to-plan 20260901-072734-001.md --plan-file='$(atk config get private_notes)/plans/2026/09/01-example-1a2b.md'",
    },
    "atk mq set-dependencies": {
        "summary": "フィードバックの明示依存だけを更新する",
        "description": "目的: 既存フィードバックの明示依存だけを更新する。\n利用場面: 先に終端すべき項目が判明したとき。依存を解除するとき。\n対象と出力: private-notesの対象ファイルのfrontmatterへ依存先のファイル名を記録し、commitとpushを行う。\n前提: 対象がinbox又はprocessingにあること。`--depends-on`を省略すると依存を全て解除する。\n復元・後始末: 変更前の依存はprivate-notesのGit履歴に残る。",
        "epilog": "実行例:\n\n  atk mq set-dependencies 20260901-072734-001.md --depends-on=20260901-081315-001.md",
    },
    "atk mq answer": {
        "summary": "TBDへ回答する",
        "description": "目的: TBDへ回答を記録し、回答待ちの依存を解除できる状態にする。\n利用場面: ユーザーが確認事項へ回答するとき。\n対象と出力: private-notesの対象TBDの回答節へ本文を書き込み、commitとpushを行う。引数を省略すると、未回答のTBDを1件ずつ表示して$EDITORで開く。\n前提: 回答欄はユーザーだけが書き込む。エージェント環境から起動した場合は書き込みを拒否する。\n復元・後始末: 記録した回答はprivate-notesのGit履歴に残る。",
        "epilog": '実行例:\n\n  atk mq answer 20260901-072734-001.md "案1を採用する"',
    },
    "atk mq commit": {
        "summary": "外部編集後の未コミット変更を確定してpushする",
        "description": "目的: 外部のエディターなどで直接編集したinboxとprocessing配下の未コミット変更を確定し、pushする。\n利用場面: `atk`以外の手段でキューのファイルを編集した後。push待ちのローカルcommitを送信するとき。\n対象と出力: private-notesのinboxとprocessing配下の変更をcommitしてpushする。差分が無い場合も滞留しているcommitをpushする。\n前提: private-notesがrebaseの途中でないこと。\n復元・後始末: commitの後の取り消しはprivate-notesのGit履歴から行う。",
        "epilog": "実行例:\n\n  atk mq commit",
    },
    "atk mq process-loop": {
        "summary": "フィードバック消化の常駐処理を開始する",
        "description": "目的: 対象リポジトリのフィードバック消化を、オーケストレーターの新規セッション起動で反復実行する常駐処理を開始する。\n利用場面: 未処理のキュー項目を無人で消化し続けるとき。\n対象と出力: `atk config`のorchestrate_model設定で決まるオーケストレーターを起動する。待機中はCIの失敗とDependabotのアラートを検出してフィードバックを投入する。対象リポジトリの作業ツリーは起動したセッションが変更する。\n前提: 対象リポジトリの現在branchが追跡先を持つこと。`--worktree`を指定すると、対象リポジトリ配下の.claude/worktrees/<NAME>にworktreeを準備する。\n復元・後始末: 前景で動作するため、停止は当該プロセスの終了で行う。作成したworktreeと起動したセッションの成果物は自動では削除しない。",
        "epilog": "実行例:\n\n  atk mq process-loop --worktree",
    },
    "atk plans": {
        "summary": "計画ファイルの保存と旧保存先からの移行",
        "description": "目的: 作業rootの計画ファイルをprivate-notesへ保存し、旧保存先の計画ファイルを現行の保存先へ移行する。\n利用場面: 実装レビューが収束した計画を保存するとき。旧保存先の計画ファイルが残る環境で保存先をそろえるとき。\n対象と出力: 作業rootの`~/.claude/plans`配下とprivate-notesのplans配下を読み書きする。サブコマンドを指定しない場合はサブコマンド一覧を標準出力へ書き、何も変更しない。\n前提: private-notesにremoteが設定されていること。\n復元・後始末: 保存と移行はcommitとpushまで行う。取り消しはprivate-notesのGit履歴から行う。",
        "epilog": "実行例:\n\n  atk plans commit 2026/09/01-example-1a2b.md",
    },
    "atk plans commit": {
        "summary": "作業中の計画バンドルを保存rootへ移してcommit・pushする",
        "description": "目的: 指定した計画のメイン、詳細、付属素材、レビュー指摘管理表を、作業rootからprivate-notesのplans配下へ移し、当該ファイルだけを対象とするcommitを作成する。\n利用場面: 実装レビューが収束し、当該計画を保存するとき。同一セッションで実装しない計画について、計画レビューが収束したとき。\n対象と出力: `~/.claude/plans`配下の同じstemのファイルをprivate-notesへ移し、当該ファイルだけを対象にcommitして既定でpushする。移した後のファイルは、移す前のファイルの作成日時と更新日時を維持する。`atk plans checkout`の取得記録がある場合は、記録した保存先へ内容を書き込んで保存側の作成日時を維持し、成功後に記録を回収する。\n前提: PLAN_FILEは計画作業root直下のメイン計画ファイル名（dd-{名称}-{16進数4桁}.md）、または保存rootからの相対メイン計画パス（yyyy/MM/dd-{名称}-{16進数4桁}.md）で指定する。\n復元・後始末: 保存先に内容の異なる同名ファイルがある場合は、何も変更せずに失敗する。取得記録がある場合は、保存元が取得時点の内容とも作業側の内容とも異なるときに、保存先と作業側のいずれも変更せずに失敗する。取得記録があり作業root直下に対象が無い場合は、保存先を変更せずに取得の記録だけを回収する。commit又はpushに失敗した場合と、移動を確定する前に失敗した場合は、作業側のファイルを保持して失敗するため、同じコマンドで再開できる。保存先と内容の異なる同名ファイルを作業root直下に持つ場合は、当該ファイルを作業root外へ退避し、`atk plans checkout`で保存済みの計画を取得してから退避した内容で置き換えて、同じコマンドを実行する。",
        "epilog": "実行例:\n\n  atk plans commit 2026/09/01-example-1a2b.md",
    },
    "atk plans checkout": {
        "summary": "保存済みの計画バンドルを作業rootへ取得する",
        "description": "目的: private-notesのplans配下へ保存済みの計画のメイン、詳細、付属素材、レビュー指摘管理表を作業root直下へ取得し、取得時点の内容を保存の照合用に記録する。\n利用場面: 保存済みの計画を再び実装するとき。保存済みの計画へ実装時の進捗を追記するとき。\n対象と出力: private-notesのplans配下を読み取り、`~/.claude/plans`直下へ同じ名前でファイルを作成する。取得したファイルの一覧を標準出力へ書く。private-notesは変更しない。\n前提: PLAN_FILEはplans rootからの相対メイン計画パスで指定する。作業root直下に同じ名前のファイルがないこと。同じ計画を取得済みでないこと。\n復元・後始末: 取得した計画は`atk plans commit`で取得元と同じ保存先へ戻す。取得を取り消す場合は、内容を変えないまま同じ`atk plans commit`を実行する。作業root直下の取得分を削除した後に同じコマンドを実行すると、保存先を変更せずに取得の記録だけを回収する。",
        "epilog": "実行例:\n\n  atk plans checkout 2026/09/01-example-1a2b.md",
    },
    "atk plans list": {
        "summary": "計画作業rootに残る計画バンドルを一覧表示する",
        "description": "目的: 計画作業rootに残る計画ファイル（メイン）を、所有セッションと計画バンドルの最終更新時刻とともに列挙し、保存先へ未反映の計画を判別できるようにする。\n利用場面: セッション終了時の通知が対象としない、他のセッションが所有する計画と所有記録を持たない計画の滞留を調べるとき。\n対象と出力: `~/.claude/plans`配下を読み取り、標準出力へ1件1行で書く。各行はメイン計画の絶対パス、所有セッション識別子、最終更新時刻をこの順にタブ区切りで並べる。所有記録が無い計画の所有セッションは`なし`と書く。ファイルは変更しない。\n前提: なし。計画作業rootが無い場合と対象が無い場合は何も出力しない。\n復元・後始末: 読み取りだけを行うため不要。",
        "epilog": "実行例:\n\n  atk plans list",
    },
    "atk plans migrate": {
        "summary": "旧保存先の計画ファイルをprivate-notesへ移行する",
        "description": "目的: `~/.claude/plans`配下に残る旧形式の計画ファイルをprivate-notesのplans配下の日付階層へ移し、本文中の旧パス参照を可搬表記へ書き換える。\n利用場面: 旧保存先の計画ファイルが残る環境で、保存先を現行の構成へそろえるとき。\n対象と出力: 旧保存先の対象ファイルを移し、キュー項目の本文に含まれる旧パスも同じcommitで書き換えてpushする。移した後のファイルは、移す前のファイルの作成日時と更新日時を維持する。移行した件数と削除した件数を標準出力へ書く。\n前提: private-notesにremoteが設定され、indexと作業ツリーがcleanであること。日付階層の正規な作業バンドルは移行の対象にしない。\n復元・後始末: commitへ到達する前に失敗した場合は変更前の状態へ戻す。移行元の移動を確定する前に失敗した場合は移行元のファイルを保持して失敗するため、原因を解消して再実行できる。移行元の書き換えを復元できない場合は、実在するファイルのパスと必要な手作業を標準エラーへ書いて失敗する。移動の確定後に一時ファイルの後始末だけが失敗した場合は、残った一時ファイルのパスを警告として書き、移行は成功として扱う。移行した後の内容はprivate-notesのGit履歴から追跡できる。",
        "epilog": "実行例:\n\n  atk plans migrate",
    },
    "atk plans rewrite-references": {
        "summary": "保存済み計画の付属ファイル参照を計画ファイル基準の表記へそろえる",
        "description": "目的: private-notesのplans配下に保存済みの計画本文へ残る可搬表記の付属ファイル参照を、計画ファイル基準が定める`~/.claude/plans/`とファイル名の表記へ書き換える。\n利用場面: 参照表記の改訂後に、保存済みの計画を現行の表記へそろえるとき。\n対象と出力: 保存済みの計画ファイル（メイン）と計画ファイル（詳細）の本文だけを読み書きし、ファイル名が当該計画のstemで始まる参照だけを書き換える。書き換えた計画の件数と参照の件数を標準出力へ書き、当該ファイルだけを対象にcommitして既定でpushする。stemが一致しない参照とキュー項目の本文は書き換えない。\n前提: private-notesにremoteが設定され、indexと作業ツリーがcleanであること。\n復元・後始末: 書き換え対象が無い場合は何も変更せず0件を出力して終わる。commitへ到達する前に失敗した場合は変更前の状態へ戻す。書き換えた後の内容はprivate-notesのGit履歴から追跡できる。",
        "epilog": "実行例:\n\n  atk plans rewrite-references",
    },
    "atk serve": {
        "summary": "フィードバック管理Web UIを起動する",
        "description": "目的: private-notesのキューをブラウザーから閲覧して操作するWebサーバーを起動する。\n利用場面: ユーザーがフィードバックの投入、編集、採否をブラウザーで行うとき。\n対象と出力: 指定したホストとポートで待機し、private-notesを読み書きする。前景で動作し、停止の要求を受領するまで終了しない。\n前提: 待受のホストとポートは、オプション、環境変数`AGENT_TOOLKIT_SERVE_HOST`と`AGENT_TOOLKIT_SERVE_PORT`、設定ファイルの順に解決する。\n復元・後始末: 停止は当該プロセスの終了で行う。ブラウザーから行った変更はprivate-notesへcommitする。",
        "epilog": "実行例:\n\n  atk serve --port=28766",
    },
    "atk config": {
        "summary": "XDG関連パスと工程別モデル設定を確認・変更する",
        "description": "目的: 設定、状態、データの各ディレクトリ、private-notesの解決結果、工程別モデル設定を確認し、変更できる設定を更新する。サブコマンドを省略した場合はshowと同じ動作をする。\n利用場面: 委譲先のモデルを切り替えるとき。コマンドが参照するパスを確認するとき。\n対象と出力: 設定ファイルと環境変数を読み取り、解決結果を標準出力へ書く。設定ファイルを変更するのは`set`だけである。\n前提: 設定ファイルはXDGの設定ディレクトリ配下に置く。存在しない場合は既定値を表示する。\n復元・後始末: `set`で変更した値は`set`で元の値へ戻す。他のサブコマンドは状態を残さない。",
        "epilog": "実行例:\n\n  atk config show\n  atk config get private_notes",
    },
    "atk config show": {
        "summary": "XDG関連パスと工程別モデル設定を一覧表示する（既定動作）",
        "description": "目的: XDG関連パスと工程別モデル設定の解決結果を`<キー>: <値>`の形式で一覧表示する。\n利用場面: 現在の設定を確認するとき。サブコマンドを省略した場合も同じ動作をする。\n対象と出力: 設定ファイルと環境変数を読み取り、標準出力へ書く。設定は変更しない。\n前提: 設定ファイルが未作成の場合も既定値を表示する。\n復元・後始末: 読み取りだけを行うため不要。",
        "epilog": "実行例:\n\n  atk config show",
    },
    "atk config get": {
        "summary": "1件以上の設定値を取得する",
        "description": "目的: 指定した1件以上の設定キーの解決値を、指定した順に1行ずつ値だけで出力する。\n利用場面: シェルのコマンド置換から`private_notes`などの解決値を取得するとき。\n対象と出力: 設定ファイルと環境変数を読み取り、標準出力へ値だけを書く。未知のキーを指定した場合は終了コード2を返す。\n前提: KEYは`atk config show`が出力するキーと同じ名前で指定する。\n復元・後始末: 読み取りだけを行うため不要。",
        "epilog": "実行例:\n\n  atk config get private_notes",
    },
    "atk config set": {
        "summary": "変更可能な設定値を更新する",
        "description": "目的: 変更できる設定値を更新して設定ファイルへ保存する。\n利用場面: 工程別のモデルと推論の深さを切り替えるとき。\n対象と出力: 設定ディレクトリの`config.json`を書き換える。設定はユーザー単位の単一値であり、全てのセッションが共有する。\n前提: KEYは変更できるキー、VALUEは`<claude|codex>:<モデル>[/<effort>]`の形式で指定する。複数の候補はASCIIカンマ区切りで並べる。\n復元・後始末: 元の値へ戻す場合は、同じコマンドで以前の値を設定する。並行して稼働するセッションへも新しい値が波及する。",
        "epilog": "実行例:\n\n  atk config set plan_model codex:gpt-5.6-sol/medium",
    },
    "atk wait-schedule": {
        "summary": "委譲待機に使うcron式を公開情報から判定する",
        "description": "目的: 指定したrequest bucketのプロンプトキャッシュTTLを公開情報から判定し、対応する壁時計のcron式を1行で出力する。\n利用場面: 委譲先や背景処理の完了を定期的に再確認するタスクを登録するとき。\n対象と出力: 環境変数と`claude auth status`の出力を読み取り、標準出力へ`*/3 * * * *`か`*/30 * * * *`を書く。ファイルは変更しない。\n前提: `--request-bucket`へmain又はsubagentを指定する。\n復元・後始末: 読み取りだけを行うため不要。出力したcron式は変更せずそのまま登録する。",
        "epilog": "実行例:\n\n  atk wait-schedule --request-bucket=main",
    },
    "atk managed-temp": {
        "summary": "管理対象一時領域を作成・列挙・後始末する",
        "description": "目的: agent-toolkitが所有権を持つ一時ディレクトリを作成し、列挙し、後始末する。\n利用場面: 大きな中間出力を作業ツリーの外へ保存するとき。残存した領域を回収するとき。\n対象と出力: agent-toolkitのデータディレクトリ配下の一時領域と登録簿を読み書きする。サブコマンドを指定しない場合はサブコマンド一覧を標準出力へ書き、何も変更しない。\n前提: 作成した領域は作成した主体が後始末する。登録済み領域は最終更新から7日を超えると`atk`の実行時に自動削除する。\n復元・後始末: 不要になった領域は`atk managed-temp cleanup`で削除する。残存した領域は`atk managed-temp list`で確認する。",
        "epilog": "実行例:\n\n  atk managed-temp create\n  atk managed-temp list",
    },
    "atk managed-temp create": {
        "summary": "管理対象一時ディレクトリを作成する",
        "description": "目的: 所有者だけが読み書きできる一時ディレクトリを作成し、その絶対パスを標準出力へ書く。\n利用場面: `atk mq show`の出力の保存など、作業ツリーを変更せずに中間結果を保存するとき。\n対象と出力: 一時rootの直下へディレクトリと管理情報ファイルを作成し、状態ディレクトリへ登録を書く。作成した絶対パスを標準出力へ書く。\n前提: `--prefix`は必須とし、受理条件は当該オプションの説明に示す。別のnamespaceへ渡す場合だけ`--root`で共有ディレクトリを指定する。\n復元・後始末: 使い終えたら`atk managed-temp cleanup --path <絶対パス>`を実行する。最終更新から7日を超えた領域は`atk`の実行時に自動で削除する。",
        "epilog": "実行例:\n\n  atk managed-temp create --prefix=mq-show",
    },
    "atk managed-temp cleanup": {
        "summary": "管理対象一時ディレクトリを後始末する",
        "description": "目的: 指定した管理対象一時ディレクトリを検証したうえで削除し、対応する登録も除去する。\n利用場面: 作成した領域を使い終えたとき。中断した後始末を再開するとき。\n対象と出力: `--path`が指すディレクトリと配下の内容を削除し、状態ディレクトリの登録を除去する。成功した場合は何も出力しない。\n前提: `--path`は作成時に返された絶対パスで指定する。実体と管理情報の双方が作成時の内容と一致することを検証する。\n復元・後始末: 削除した内容は復元できない。登録だけを失った領域は、`--recover-registry`を指定した場合に限り実体側の管理情報から登録を復元して後始末する。",
        "epilog": "実行例:\n\n  atk managed-temp cleanup --path=/tmp/mq-show-abcd1234",
    },
    "atk managed-temp list": {
        "summary": "管理対象一時ディレクトリを列挙する",
        "description": "目的: 検証を通過した管理対象一時領域を作成時刻の順に1件1行のJSONで列挙し、回収の候補を警告として報告する。\n利用場面: 残存している領域を把握するとき。登録と実体が一致しない領域の回収手順を確認するとき。\n対象と出力: 状態ディレクトリの登録と各領域を読み取り、標準出力へJSONを書く。実体の消滅を確定できた登録だけを削除し、確定できない登録は保持して標準エラーへ報告する。該当が0件のときは終了コード1を返す。\n前提: `--prefix`を指定すると、当該prefixの領域だけを対象にする。\n復元・後始末: 報告された領域は`atk managed-temp cleanup --path <絶対パス>`で後始末する。実体へ到達できない登録は、同じ絶対パスへ到達できる実行文脈で本コマンドを再実行すると回収する。",
        "epilog": "実行例:\n\n  atk managed-temp list --prefix=mq-show",
    },
    "atk worktree-stash": {
        "summary": "worktree固有refへ変更を退避する",
        "description": "目的: 複数のworktreeが共有する`refs/stash`を直接操作せず、退避物をworktree固有のrefへ記録して安全に扱う。\n利用場面: 並行して別のworktreeが動く環境で、承認された退避が必要になったとき。\n対象と出力: 現在のworktreeの未コミット変更と未追跡ファイルを`refs/worktree/<ラベル>`へ記録し、又は当該refを削除する。共有する`refs/stash`は変更しない。\n前提: カレントディレクトリがGitの作業ツリーであること。private-notesの作業ツリーでは実行しない。\n復元・後始末: 復元は`git stash apply --index refs/worktree/<ラベル>`で行い、不要になったrefは`atk worktree-stash drop`で削除する。",
        "epilog": "実行例:\n\n  atk worktree-stash save --label <ラベル>\n  git stash apply --index refs/worktree/<ラベル>\n  atk worktree-stash drop refs/worktree/<ラベル>\n\nprivate-notesリポジトリの作業ツリーは本コマンドの対象にしない。private-notesの未コミットのキュー操作は`atk mq commit`で確定する。",
    },
    "atk worktree-stash save": {
        "summary": "現在worktreeの変更をworktree固有refへ退避する",
        "description": "目的: 現在のworktreeの未コミット変更と未追跡ファイルを、worktree固有のref`refs/worktree/<ラベル>`へ記録し、共有する`refs/stash`からは取り除く。\n利用場面: 複数のworktreeが並行する環境で退避が必要なとき。通常の`git stash push`は全worktreeが共有する`refs/stash`を動かすため用いない。\n対象と出力: Git共通ディレクトリの固定ロックを取得し、退避を作成してworktree固有refへ記録し、作成した共有stashを取り除く。記録したref名を標準出力へ書く。退避の対象が無い場合と同じ名前のrefが既にある場合は終了コード2を返す。\n前提: `--label`はrefとして有効な文字列で指定する。private-notesリポジトリの作業ツリーでは実行できない。\n復元・後始末: `git stash apply --index refs/worktree/<ラベル>`で復元し、不要になったら`atk worktree-stash drop refs/worktree/<ラベル>`で削除する。途中で失敗した場合は退避物を削除せず、復旧に使う識別子を標準エラーへ書く。",
        "epilog": "実行例:\n\n  atk worktree-stash save --label=before-rebase",
    },
    "atk worktree-stash drop": {
        "summary": "退避識別子をOID照合して削除する",
        "description": "目的: worktree固有refまたは共有stashの退避物を、現在指しているOIDを照合したうえで削除する。\n利用場面: 復元済み、又は不要と判断した退避物を取り除くとき。\n対象と出力: Git共通ディレクトリの固定ロックを取得し、指定した識別子が現在指すOIDを確認してから削除する。削除した識別子を標準出力へ書く。\n前提: 識別子は`refs/worktree/<ラベル>`か`stash@{<番号>}`の形式で指定する。private-notesリポジトリの作業ツリーでは実行できない。\n復元・後始末: 削除した退避物は復元できない。復元が必要な内容は、削除の前に`git stash apply`で取り出す。",
        "epilog": "実行例:\n\n  atk worktree-stash drop refs/worktree/before-rebase",
    },
    "atk watch": {
        "summary": "委譲先の成果物側の状況を1行で出力する",
        "description": "目的: 指定した作業ツリーの未コミット差分の件数とHEAD、指定したファイルの行数と最終更新からの経過秒を1行へまとめて出力する。\n利用場面: 委譲先の作業が進んでいるかを、少ないコンテキストで繰り返し観測するとき。\n対象と出力: 指定した作業ツリーとファイルを読み取り、標準出力へ1行で書く。取得できない項目がある場合は終了コード1、指定が不正な場合は終了コード2を返す。\n前提: `--worktree`か`--file`を1件以上指定する。ラベルは対象ごとに重複させない。作業ツリーの差分の件数とHEADは、並行する他の主体の変更も含む全体の値である。\n復元・後始末: 読み取りだけを行うため不要。",
        "epilog": "実行例:\n\n  atk watch --worktree=lane-05=/home/aki/dotfiles/.claude/worktrees/lane-05",
    },
    "atk review-table": {
        "summary": "レビュー指摘管理表（8列TSV）を操作する",
        "description": "目的: 計画レビューと実装レビューの指摘、指摘レベル、採否、対応内容を8列のTSVへ排他的に記録する。\n利用場面: レビュー担当が指摘を追加するとき。レビューイーが応答を記録するとき。\n対象と出力: 指定したTSVファイルを読み書きする。サブコマンドを指定しない場合はサブコマンド一覧を標準出力へ書き、何も変更しない。\n前提: 表のパスは呼び出し元が指定する。同時更新は本コマンドが排他制御する。\n復元・後始末: 記録した行の取り消しは、表を保管するリポジトリのGit履歴から行う。",
        "epilog": "実行例:\n\n  atk review-table show <表のパス>\n\n列は`round`、`track`、`location`、`issue`、`level`、`response-needed`、`response`、`no-response-reason`の順とする。`level`は`要件`、`仕様`、`詳細`、`実装`のいずれかを指定する。`track`は新規の経路では`plan-review`か`implementation-review`を指定し、`plan-conformance`と`independent`は保存済みの表の読み取り互換として扱う。保存済みの7列形式は`level`を空として読み込み、更新時に8列形式へ書き戻す。",
    },
    "atk review-table init": {
        "summary": "空のレビュー表を作成する",
        "description": "目的: 行を持たない空のレビュー指摘管理表を作成し、作成したパスを標準出力へ書く。\n利用場面: レビューを開始する前に、計画ファイルと同じstemの表を用意するとき。\n対象と出力: 指定したパスへTSVファイルを作成する。同じパスに表が既にある場合は、何も変更せずに失敗する。\n前提: pathは計画ファイルと同じディレクトリの`<計画stem>.plan-review.tsv`か`<計画stem>.exec-review.tsv`で指定する。\n復元・後始末: 誤って作成した表は、当該ファイルを削除して取り除く。",
        "epilog": "実行例:\n\n  atk review-table init /home/aki/.claude/plans/2026/09/01-example-1a2b.plan-review.tsv",
    },
    "atk review-table add": {
        "summary": "レビュー担当の指摘を追加する",
        "description": "目的: レビュー担当の指摘を1行追加する。\n利用場面: レビューで実在の指摘を確定したとき。\n対象と出力: 指定した表をロックして1行を追加する。各セルはJSON文字列として保存する。\n前提: `--round`、`--track`及び`--level`を指定し、指摘箇所と指摘内容を位置引数か対応するオプションで与える。\n復元・後始末: 追加した行の応答は`atk review-table respond`で更新する。",
        "epilog": '実行例:\n\n  atk review-table add /home/aki/.claude/plans/2026/09/01-example-1a2b.plan-review.tsv --round=1 --track=plan-review --level=詳細 "実装資料" "検索コマンドが未記載"',
    },
    "atk review-table respond": {
        "summary": "レビューイーの応答を更新する",
        "description": "目的: 行を一意に特定できる列を指定して、レビューイーの採否と対応内容を更新する。\n利用場面: 指摘への採否を確定し、対応の内容か対応が不要である理由を記録するとき。\n対象と出力: 指定した表をロックして該当する行を更新する。特定できる行が無い場合と複数ある場合は失敗する。\n前提: `round`、`track`、`location`、`issue`のうち、行を一意に特定できる列を指定する。`--issue`には復号した後の本文を渡す。\n復元・後始末: 誤った更新は、同じコマンドで正しい値へ上書きする。",
        "epilog": '実行例:\n\n  atk review-table respond /home/aki/.claude/plans/2026/09/01-example-1a2b.plan-review.tsv --round=1 --track=plan-review --response-needed=yes --response="検索コマンドを追記した"',
    },
    "atk review-table show": {
        "summary": "レビュー表を表示する",
        "description": "目的: レビュー指摘管理表を保存順のまま表示する。各セルはJSON文字列として保存されており、復号せずに書き"
        "\u51fa\u3059。\n利用場面: 未解消の指摘と対応の状況を確認するとき。\n対象と出力: 指定した表を読み取り、標準出力へ書く。ファイルは変更しない。\n前提: `--track`を指定すると、当該trackの行だけを表示する。\n復元・後始末: 読み取りだけを行うため不要。",
        "epilog": "実行例:\n\n  atk review-table show /home/aki/.claude/plans/2026/09/01-example-1a2b.plan-review.tsv",
    },
    "atk review-table validate": {
        "summary": "レビュー表を検証する",
        "description": "目的: レビュー指摘管理表の列数、複合キー、応答の充足を検証する。\n利用場面: レビューの収束を判定する前に、表の構造と未応答の行を確認するとき。\n対象と出力: 指定した表を読み取り、違反がある場合はその内容を標準エラーへ書いて非0の終了コードを返す。ファイルは変更しない。\n前提: `--allow-unanswered`を指定すると、未応答の行を許容して構造だけを検証する。\n復元・後始末: 読み取りだけを行うため不要。",
        "epilog": "実行例:\n\n  atk review-table validate /home/aki/.claude/plans/2026/09/01-example-1a2b.plan-review.tsv",
    },
}


class JapaneseHelpFormatter(argparse.HelpFormatter):
    """日本語の定型見出しを使い、識別子を途中で分割せずに折り返す。"""

    def _format_usage(self, *args: Any, **kwargs: Any) -> str:
        return super()._format_usage(*args, **kwargs).replace("usage: ", "使い方: ", 1)

    def _split_lines(self, text: str, width: int) -> list[str]:
        return textwrap.wrap(text, width, break_long_words=False, break_on_hyphens=False)

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        wrapper = textwrap.TextWrapper(
            width=width,
            initial_indent=indent,
            subsequent_indent=indent,
            break_long_words=False,
            break_on_hyphens=False,
        )
        return "\n".join(wrapper.fill(line) if line else "" for line in text.splitlines())


def _configure_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser._positionals.title = "位置引数"  # pylint: disable=protected-access
    parser._optionals.title = "オプション"  # pylint: disable=protected-access
    parser.add_argument("-h", "--help", action="help", help="このヘルプを表示して終了する")
    return parser


def create_root_parser(prog: str, *, description: str, epilog: str) -> argparse.ArgumentParser:
    """日本語のヘルプ書式を適用したルートparserを生成する。"""
    return _configure_parser(
        argparse.ArgumentParser(
            prog=prog,
            description=description,
            epilog=epilog,
            formatter_class=JapaneseHelpFormatter,
            add_help=False,
        )
    )


def add_subcommands(
    parser: argparse.ArgumentParser,
    *,
    dest: str,
    required: bool = True,
    show_help_when_missing: bool = False,
    parser_class: type[argparse.ArgumentParser] | None = None,
) -> argparse._SubParsersAction:
    """日本語の説明を持つサブparser群を追加する。"""
    if show_help_when_missing and required:
        raise ValueError("show_help_when_missingにはrequired=Falseが必要です。")
    kwargs: dict[str, Any] = {
        "dest": dest,
        "required": required,
        "title": "位置引数",
        "description": "実行するサブコマンド",
    }
    if parser_class is not None:
        kwargs["parser_class"] = parser_class
    if show_help_when_missing:
        parser.set_defaults(_help_parser=parser)
    return parser.add_subparsers(**kwargs)


def add_command(
    subparsers: argparse._SubParsersAction,
    name: str,
    *,
    summary: str,
    description: str,
    epilog: str | None = None,
    **kwargs: Any,
) -> Any:
    """一覧用の要約と自身の説明を持つサブコマンドを登録する。"""
    parser = subparsers.add_parser(
        name,
        help=summary,
        description=description,
        epilog=epilog,
        formatter_class=JapaneseHelpFormatter,
        add_help=False,
        **kwargs,
    )
    parser = _configure_parser(parser)
    parser.set_defaults(_help_parser=None)
    return parser
