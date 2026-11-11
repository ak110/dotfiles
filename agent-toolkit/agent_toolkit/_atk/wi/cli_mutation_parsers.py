"""WIの投入・編集・状態遷移の引数登録。"""

from typing import Any

from agent_toolkit._atk.wi.cli_parser_support import (
    _active_filename_completer,
    _add_draft_comparison_arg,
    _cooldown_days,
    _editable_filename_completer,
    _hold_filename_completer,
    _holdable_filename_completer,
    _inbox_filename_completer,
    _removable_filename_completer,
    _returnable_filename_completer,
)
lazy from agent_toolkit._atk import help_text as _atk_help
lazy from agent_toolkit._atk.wi import arguments as _arguments
lazy from agent_toolkit._atk.wi import constants as _wi_constants


def _add_wi_add_parser(sub: Any) -> None:
    """投入サブコマンドを登録する。"""
    add = _atk_help.add_command(sub, "add", **_atk_help.HELP["atk wi add"])
    add.add_argument(
        "--body-file",
        metavar="PATH",
        action="append",
        default=None,
        help=(
            "本文を記載したファイルのパス。複数回指定すると複数件を投入する。"
            "引用符・改行を含む長文をシェルのエスケープを介さずに渡す場合に使う。"
        ),
    )
    add.add_argument(
        "--batch",
        action="store_true",
        help=(
            "`atk wi show --all`の出力形式で複数エントリを一括登録する。"
            "移行・復元用途であり、frontmatter・本文を原文保持で取り込み"
            "（target_commitの再取得・UWI見出しの再生成を行わない）、"
            "ファイル名は取り込み先と衝突しない限り元名を維持する。"
            "対象リポジトリは各エントリのfrontmatterのtarget_repoだけを用いる。"
            "--type・--scope・--question-type・--choices・--depends-on・"
            "--cooldown-until・--target-repo・--sourceとは併用できない。"
            "show形式は可逆な直列化ではないため、本文が完全なshow形式エントリの引用を含む場合に"
            "エントリ境界を誤って分割し得る点と、元ファイル末尾の改行の有無・連続空行・"
            "構造見出し（`# awi`・`# uwi`・`## target_repo: ...`）と同形の末尾行を"
            "復元できない点は限界として許容する。"
            "改行はCRLF・単独CRを含む入力もLFへ正規化して保存する。"
        ),
    )
    add.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "本文と引数の検証だけを行い、private-notes、remoteおよび対象リポジトリのいずれも変更せずに終了する。"
            "検証が成立しない場合は終了コード1で終わる。--batchとは併用できない。"
        ),
    )
    add.add_argument(
        "--type",
        choices=_wi_constants.WI_TYPES,
        default=None,
        help="投入する種別。省略時はawiとして投入する。uwiを指定すると確認事項として投入する。",
    )
    add.add_argument(
        "--scope",
        metavar="NAME",
        default=None,
        help="UWIの適用範囲を表す識別子。`--type=uwi`でのみ指定できる。",
    )
    add.add_argument(
        "--question-type",
        metavar="{" + ",".join(_wi_constants.NEW_QUESTION_TYPES) + "}",
        default=None,
        help=(
            "UWIの回答形式。`--type=uwi`では必須とし、`--type=uwi`でのみ指定できる。"
            "選択肢から選ぶ問いは`choice`（`--choices`も指定する）、2択の可否を問う問いは`yes-no`とする。"
            "実施済みの対応への事後承認は`post-approval`とし、固定二択を使う。"
            "選択肢に無い回答は回答欄で受ける。"
        ),
    )
    add.add_argument(
        "--choices",
        metavar="A,B,C",
        action="append",
        default=None,
        help=(
            "UWIの選択肢を指定順に保存する。`--question-type=choice`で必要となる。"
            "ASCIIカンマ区切り（--choices A,B,C）と反復指定（--choices A --choices B,C）を使える。"
        ),
    )
    add.add_argument(
        "--depends-on",
        metavar="FILENAME",
        action="append",
        default=None,
        help="AWIが処理完了を待つキュー項目。--type=awiでのみ指定でき、複数回指定できる。",
    )
    add.add_argument(
        "--cooldown-until",
        metavar="DATETIME",
        default=None,
        help=(
            "通常AWIの初回保存へ冷却期限を設定する。タイムゾーン付きISO 8601日時を指定し、"
            "複数の--body-fileへ共通適用する。空文字列・UWI・--batchとの併用は拒否する。"
            "本文frontmatterのcooldown_untilでは指定できない。"
        ),
    )
    add.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        help=(
            "投入元の識別子をfrontmatterのsourceへ記録する。"
            "エージェントは起動スキル名（プラグイン接頭辞を除く）、スキルが無い場合はagentを指定する。"
            "エージェント環境の通常追加ではhumanを拒否する。人間の直接投入ではsourceを省略する。"
            "本文先頭のfrontmatterに source がある場合は本オプションより優先する。"
        ),
    )
    _arguments.add_target_repo_arg(
        add,
        help_extra=(
            "ローカルパス指定時は指定worktree、正規化リモートURL指定時は"
            "ローカルHEADを持たないリポジトリ識別子として解決する。"
            "実行元worktreeのorigin以外の登録リモートだけに一致するURLは警告付きで受理する。"
            "frontmatterにtarget_repoが明示されていない場合のfallback値として扱う。"
        ),
        resolved_by_consumer=True,
    )
    add.set_defaults(subparser=add)
    _add_draft_comparison_arg(add)


def _add_mq_transition_parsers(sub: Any) -> None:
    """状態遷移・削除サブコマンドを登録する。"""
    start_processing = _atk_help.add_command(sub, "start-processing", **_atk_help.HELP["atk wi start-processing"])
    start_processing.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="処理開始するAWIまたはUWIのファイル名。--allと併用せず、個別指定では1個以上を指定する。",
    ).completer = _inbox_filename_completer  # type: ignore[attr-defined]
    _arguments.add_bulk_transition_args(start_processing, action_label="処理開始")
    _arguments.add_target_repo_arg(
        start_processing,
        help_extra="個別指定時はfrontmatterと一致するか検証し、--all指定時は対象を限定する。",
        multiple=True,
    )
    start_processing.set_defaults(subparser=start_processing)

    hold = _atk_help.add_command(sub, "hold", **_atk_help.HELP["atk wi hold"])
    hold.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="保留するファイル名。--allと併用せず、個別指定では1個以上を指定する。",
    ).completer = _holdable_filename_completer
    hold.add_argument(
        "--state",
        choices=(_wi_constants.WI_STATE_PROCESSING, _wi_constants.WI_STATE_ADOPTED, _wi_constants.WI_STATE_REJECTED),
        default=None,
        help=(
            "終了した項目、またはエージェント環境で処理中の項目を保留する場合に指定する。"
            "省略時はinboxまたはprocessingから保留する。ただしエージェント環境ではprocessingの項目を保留せず失敗する。"
            "--allとは併用できない。"
        ),
    )
    _arguments.add_bulk_transition_args(hold, action_label="保留")
    _arguments.add_target_repo_arg(
        hold,
        help_extra="個別指定時はfrontmatterと一致するか検証し、--all指定時は対象を限定する。",
        multiple=True,
    )
    hold.set_defaults(subparser=hold)

    unhold = _atk_help.add_command(sub, "unhold", **_atk_help.HELP["atk wi unhold"])
    unhold.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="保留を解除するファイル名。--allと併用せず、個別指定では1個以上を指定する。",
    ).completer = _hold_filename_completer
    _arguments.add_bulk_transition_args(unhold, action_label="保留解除")
    _arguments.add_target_repo_arg(
        unhold,
        help_extra="個別指定時はfrontmatterと一致するか検証し、--all指定時は対象を限定する。",
        multiple=True,
    )
    unhold.set_defaults(subparser=unhold)

    return_to_inbox = _atk_help.add_command(sub, "return-to-inbox", **_atk_help.HELP["atk wi return-to-inbox"])
    return_to_inbox.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="差し戻すファイル名。--allと併用せず、個別指定では1個以上を指定する。",
    ).completer = _returnable_filename_completer  # type: ignore[attr-defined]
    return_to_inbox.add_argument(
        "--cooldown-days",
        type=_cooldown_days,
        default=None,
        metavar="DAYS",
        help="時間経過だけで解除される待機のため、指定日数（3以上）だけ再処理対象から除外する。",
    )
    return_to_inbox.add_argument(
        "--state",
        choices=(_wi_constants.WI_STATE_REJECTED, _wi_constants.WI_STATE_ADOPTED),
        default=None,
        help="adoptedまたはrejectedから差し戻す場合に指定する。省略時はprocessingから差し戻す。--allとは併用できない。",
    )
    _arguments.add_bulk_transition_args(return_to_inbox, action_label="差し戻し")
    _arguments.add_target_repo_arg(
        return_to_inbox,
        help_extra="個別指定時はfrontmatterと一致するか検証し、--all指定時は対象を限定する。",
        multiple=True,
    )
    return_to_inbox.set_defaults(subparser=return_to_inbox)

    _arguments.add_adopt_parser(sub)

    reject = _atk_help.add_command(sub, "reject", **_atk_help.HELP["atk wi reject"])
    reject.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="不採用とするファイル名。--allと併用せず、個別指定では1個以上を指定する。",
    ).completer = _active_filename_completer  # type: ignore[attr-defined]
    _arguments.add_note_args(
        reject,
        help_text="不採用理由のメモ（本文末尾の`## 処理結果`節へ追記する）。--note=VALUE形式で渡すことを推奨。",
    )
    reject.add_argument(
        "--commit",
        metavar="SHA",
        default=None,
        help=(
            "ローカルworktreeとrevisionを検証し、一意な長さの短縮OIDと件名を記録する。短縮OIDと完全OIDのどちらも受理する。"
            "worktreeまたはrevisionを解決できない場合は終了コード2で状態変更前に停止する。"
            "対象worktreeの絶対パスを--target-repoへ指定して再実行する。"
            "--commit=VALUE形式で渡すことを推奨。"
        ),
    )
    reject.add_argument(
        "--skip-push",
        action="store_true",
        help="管理リポジトリへのpushを省略してcommitだけ行う（連続操作の中間で用い、最後の操作では指定しない）。",
    )
    reject.add_argument(
        "--if-inbox",
        action="store_true",
        help=(
            "pull後も全対象がinboxにある場合だけ不採用とし、processingへ移った対象があれば全体を変更しない。"
            "--allとは併用できない。"
        ),
    )
    _arguments.add_bulk_transition_args(reject, action_label="不採用")
    _arguments.add_target_repo_arg(
        reject,
        help_extra="個別指定時はfrontmatterと一致するか検証し、--all指定時は対象を限定する。",
        multiple=True,
    )
    reject.set_defaults(subparser=reject)

    rm = _atk_help.add_command(sub, "rm", **_atk_help.HELP["atk wi rm"])
    rm.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="削除するファイル名。--allと併用せず、個別削除では1個以上を指定する。",
    ).completer = _removable_filename_completer  # type: ignore[attr-defined]
    _arguments.add_bulk_transition_args(rm, action_label="削除")
    rm.add_argument(
        "--state",
        choices=_wi_constants.WI_STATES,
        default=None,
        help="個別削除で探索する状態を明示する。--allとは併用できない。",
    )
    rm.add_argument(
        "--force",
        action="store_true",
        help="processing状態のファイルも削除する（指定しない場合は保護して削除を拒否する）。",
    )
    _arguments.add_note_args(
        rm,
        help_text="削除の理由を記録するメモ。`--note=VALUE`の形式で渡すことを推奨する。",
    )
    _arguments.add_target_repo_arg(
        rm,
        help_extra="個別指定時はfrontmatterと一致するか検証し、--all指定時は削除対象を限定する。",
        multiple=True,
    )
    rm.set_defaults(subparser=rm)


def _add_mq_edit_parsers(sub: Any) -> None:
    """本文編集・計画変換・依存更新サブコマンドを登録する。"""
    effect = _atk_help.add_command(sub, "effect-check", **_atk_help.HELP["atk wi effect-check"])
    effect.add_argument("filenames", metavar="FILENAME", nargs="+", help="効果を記録する採用済みAWIのファイル名（1件以上）")
    effect.add_argument("--worktree", required=True, help="対応commitの変更ファイルを確かめる対象リポジトリの作業ツリー")
    effect.add_argument("--file", help="対応commitで変更したリポジトリ相対パス。省略時は作業項目の全体へ判定を記録する。")
    effect.add_argument(
        "--verdict", required=True, choices=("pending", "confirmed", "insufficient", "not_required"), help="効果の判定"
    )
    effect.add_argument("--checked-on", required=True, help="判定日（YYYY-MM-DD）")
    effect.add_argument("--review-record", required=True, help="判定した振り返りの記録位置")
    effect.add_argument("--corrective-awi", help="効果不足の是正先AWIの保存済みファイル名")
    effect.add_argument("--reason", help="判定の理由（pending・not_requiredでは必須）")
    edit = _atk_help.add_command(sub, "edit", **_atk_help.HELP["atk wi edit"])
    edit.add_argument(
        "filename",
        metavar="FILENAME",
        nargs="?",
        default=None,
        help=(
            "編集対象のファイル名（inbox・processing・holdいずれも対象）。"
            "--body-fileとともに指定すると非対話で編集する。"
            "省略時はinbox配下で最終追加のファイル（ファイル名順で最大）を$EDITORで編集する。"
        ),
    ).completer = _editable_filename_completer  # type: ignore[attr-defined]
    edit.add_argument(
        "--body-file",
        metavar="PATH",
        default=None,
        help=(
            "UTF-8ファイルの内容を本文として読み込む。"
            "先頭frontmatterで明示したメタデータだけを更新し、未指定メタデータを保持する。"
        ),
    )
    edit.add_argument(
        "--append",
        action="store_true",
        help="FILENAMEの元のraw bytesを保ち、--body-fileの本文をUTF-8で末尾へ追記する。UWIは対象外。",
    )
    edit.add_argument(
        "--cooldown-until",
        metavar="DATETIME",
        default=None,
        help=(
            "inbox・holdのAWIの再処理抑制期限をタイムゾーン付きISO 8601日時で設定する。"
            "空文字列で解除する。UWIでは空文字列による解除だけを受け付ける。"
        ),
    )
    edit.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "FILENAMEと--body-fileによる本文置換を保存と同じ検証にかけ、保存せずに結果を返す。"
            "private-notesとremoteを変えない。--append・--cooldown-untilとは併用できない。"
        ),
    )
    _arguments.add_target_repo_arg(edit, help_extra="指定時は対象ファイル名のfrontmatterと一致するか検証する。")
    edit.set_defaults(subparser=edit)
    _add_draft_comparison_arg(edit)

    set_dependencies = _atk_help.add_command(sub, "set-dependencies", **_atk_help.HELP["atk wi set-dependencies"])
    set_dependencies.add_argument(
        "filename",
        metavar="FILENAME",
        help="更新する`inbox`・`processing`・`hold`のいずれかにあるAWIファイル名。",
    ).completer = _active_filename_completer  # type: ignore[attr-defined]
    set_dependencies.add_argument(
        "--depends-on",
        metavar="FILENAME",
        action="append",
        default=None,
        help="処理完了を待つキュー項目。複数回指定でき、省略時は依存を全て解除する。",
    )
    _arguments.add_target_repo_arg(set_dependencies, help_extra="省略時は現在の作業リポジトリと一致するか確かめる。")
