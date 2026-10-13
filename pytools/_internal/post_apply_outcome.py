"""post-applyステップの構造化結果を定義する。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PostApplyNotice:
    """post-apply完了時に表示する案内を表す。"""

    message: str
    command: str | None = None


@dataclass(frozen=True)
class PostApplyOutcome:
    """post-applyステップの結果。全ステップの`run`はこの型だけを返す。

    `failure`が`None`でなければ「失敗」、`None`なら`changed`の真偽で「更新」か「スキップ」と数える。
    post-applyは失敗が1件でもあれば終了コード1、無ければ0で終わる。
    失敗には理由と次の操作を指定し、どちらかを欠く結果は作成時に拒否する。

    失敗の分類は、ステップの単位ではなく失敗した操作の種類で判定する。
    1つのステップが取得と設定の書き換えの両方を行う場合は、失敗した操作ごとに分類する。

    - 外部のツール、plugin、バイナリ、依存の取得と導入（ネットワーク越しの取得、
      パッケージマネージャーによる導入と更新、uv環境の構築）ができない場合は、警告を出力して「スキップ」を返す。
      ネットワークの不達などdotfilesユーザーが対処できない理由で毎回の終了コードを1にしないためである。
    - 設定ファイル、環境変数、レジストリ、リンク、systemd unit、配布先のファイルの書き換えと撤去が失敗した場合は、
      `failure`へ内容を入れて「失敗」を返すか、例外を送出する。配布した設定が反映されない状態を、post-applyが終了コードでdotfilesユーザーへ伝えるためである。
    - ステップが使うツールが無いために処理できない場合（先行ステップの取得・導入がスキップされた場合を含む）は
      「スキップ」とする。
    - 予期しない例外は、`post_apply`が「失敗」と数える。
    """

    changed: bool = False
    notices: tuple[PostApplyNotice, ...] = ()
    # 完了時に標準出力へ表示する推奨コマンド。
    recommendations: tuple[str, ...] = ()
    failure: str | None = None
    next_action: str | None = None

    def __post_init__(self) -> None:
        """失敗理由と次の操作が揃った結果だけを受け取る。"""
        if self.failure is None:
            if self.next_action is not None:
                raise ValueError("次の操作は失敗理由とともに指定する")
            return
        if not isinstance(self.failure, str) or not self.failure.strip():
            raise ValueError("失敗理由には空でない文字列を指定する")
        if not isinstance(self.next_action, str) or not self.next_action.strip():
            raise ValueError("失敗時の次の操作には空でない文字列を指定する")
