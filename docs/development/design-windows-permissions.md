# Windowsの昇格と状態ディレクトリの権限の設計記録

[設計記録の索引](design.md)から参照する、同じユーザーの昇格したプロセスと非昇格のプロセスが状態を共有するための記録である。

## 所有者だけが使うディレクトリの作成

agent-toolkitの状態ディレクトリとmanaged-tempは、POSIXでは0o700で作成し、Windowsでは親（`%LOCALAPPDATA%`などユーザーprofile配下）から継承するACLで作成する。
作成は`agent_toolkit._common.private_directory`へ集め、`private_directory_invariant_test.py`が0o700の`mkdir`と`mkdtemp`の直接の呼び出しを拒否する。
Windowsの`os.mkdir`はmodeが0o700のとき`D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)`の保護付きDACLを付け、`tempfile.mkdtemp`も0o700を渡す。
OWNER RIGHTSは対象の所有者を指し、昇格したプロセスが作成したファイルの所有者は`BUILTIN\Administrators`になるため、同じユーザーの非昇格のプロセスは読めない（2026年11月19日、管理サービスの世代索引で発生）。
ユーザーprofile配下の継承するACLはユーザー・SYSTEM・Administratorsに限られるため、継承に任せても他のユーザーへは公開しない。
作成後に保護付きDACLを継承へ付け替える案は、作成のたびにACLの操作と失敗の分岐を加えるため採らない。

## 読めない状態ファイルの修復

同じユーザーの通常のプロセスが読めない状態ファイルと旧版の保護付きDACLは、更新後処理の工程（`pytools/_internal/restore_agent_toolkit_acl.py`）が継承へ戻す。
同工程は`%LOCALAPPDATA%\agent-toolkit`配下の全項目を`windows_security.restore_inheritance`で判定し、次の2種類を戻す。

- 同じユーザーの通常のプロセスが読めない項目。
  `AccessCheck`で判定し、UACで通常のプロセスが非昇格で動く環境の昇格した実行では、Administratorsを拒否専用にしたtokenで通常のプロセスを表す。
- 保護付きDACLへ旧版のOWNER RIGHTSのACEを明示したディレクトリ。
  配下の読取を所有者に依存させ、昇格したプロセスが書いた配下を非昇格のプロセスから閉ざすためである。

2026年11月19日、sthenoには、所有者がユーザーでSYSTEMとAdministratorsだけを許す保護付きのACL（`O:<ユーザー>D:P(A;;FA;;;SY)(A;;FA;;;BA)`）の項目と、その形のディレクトリから継承した子が4,985件残った。
非昇格の管理サービスは世代索引を読めずに起動できなかった。
旧版のACEを明示したディレクトリだけを対象にした判定は、この形を直さなかった。
OWNER RIGHTSのACEが無ければ、所有者は暗黙にREAD_CONTROLとWRITE_DACを持つ（Microsoftの資料「Owner of a New Object」と、MS-DTYPのOWNER_RIGHTSの定義）。
所有者がユーザーの項目を非昇格の実行で戻せることは、Administratorsを拒否専用にしたtokenを使うWindowsのCIの試験で確かめた。
ただし`CreateFileW`は要求にSYNCHRONIZEを加え（`FILE_FLAG_OVERLAPPED`を付けても同じ）、所有者の暗黙の権限だけでは開けなかった。
変更するハンドルは`NtOpenFile`でREAD_CONTROL・WRITE_DAC・FILE_READ_ATTRIBUTESだけを求めて開く。
FILE_READ_ATTRIBUTESは、項目のACLが許可しなくても親ディレクトリを列挙できる呼出元に許可された（2026年11月19日、WindowsのCIで観測）。
所有者がAdministratorsで読めない項目は、非昇格の実行では変更せず、管理者として起動した端末での`update-dotfiles`の実行を案内する。
非昇格のtokenで親の保護を外すと、OSは書けない配下を更新しないまま成功を返すが、全項目を判定するため、残った配下も同じ走査で読めない項目として見つかる。
戻しても親から継承するACEがユーザーに許可しない項目は、戻したうえで失敗として報告する。
`%LOCALAPPDATA%`はユーザーへ継承するACLを持つため、上から順に戻す走査ではこの場合は起きない。
`icacls /reset /T`はjunctionの先までたどりうるため案内しない。

旧版のACEは保護付きDACLのディレクトリだけを対象にし、継承したOWNER RIGHTSのACEは対象にしない。
WindowsのCIでは更新検証のhomeが`tempfile.mkdtemp`の配下にあり、継承したACEも対象にした版は戻した後も全配下を対象と誤認し、2823件の配下ごとに走査と再計算を繰り返して93.7秒を要した。
明示したACEだけを対象にした版は、同じ更新検証で5件を戻して5.7秒で終え、明示したOWNER RIGHTSのACEは残らなかった。
ACLは`GetFileSecurityW`でOSの記録のまま読む。
親が自動継承の印を持たないと、記録では継承したACEにも`ID`が付かず、明示したACEと区別できない（2026年11月19日、sthenoで観測）。
`GetNamedSecurityInfoW`は親と比べて`ID`を補うが、1項目あたりの所要が約2倍のため使わず、記録のままでも区別できる保護の有無で判定する。
走査はリンクとjunctionをたどらず、OSの継承の再計算がjunctionの先のACLを変えないこともCIで確かめた。
変更する対象は、reparse pointをたどらずに開いたハンドルで判定し直し、同じハンドルで変更する。
昇格した実行の判定と変更の間に、非昇格のプロセスが対象をjunctionへ差し替えても、差し替え先のACLを変えないためである。
パスの途中の要素の差し替えとハードリンクにも備え、ハンドルの最終パスが走査したパスと一致しない項目と、ほかのハードリンクを持つファイルは変更しない。
全項目を対象にした判定では、root外の管理者だけが読める項目の大半が「通常のプロセスが読めない項目」に当たるためである。
ディレクトリを戻したときのOSによる配下の継承の再計算が、配下のファイルのハードリンクを通じてroot外の実体のACLを変えうる点は対処していない（推論、未検証）。
pytestは一時領域を0o700で作成するため、managed-temp配下の試験の一時領域も修復の対象になる。

全項目の判定は、階層ごとにACLを8スレッドで並行して読み、同じ記述子の判定を使い回す。
sthenoの約12万項目（ディレクトリ13,535件、ファイル108,012件）では3.7秒で、ディレクトリだけを判定した旧版は3.3秒だった。
WindowsのCIでは、空のディレクトリ5051件の走査とACLの読取が0.62秒だったため、更新のたびに走査する。
修復を管理サービスの起動時に行う案は、起動の上限30秒の内側で配下全体を走査することになり、昇格した実行が書く前に戻す順序も保証できないため採らない。

## 昇格した更新での管理サービスの扱い

UACの管理者承認モードで動くユーザーがWindowsで昇格したプロセスから`update-dotfiles`を実行した場合、agents_serverのウォームアップは依存の準備だけを行う。
管理サービスの起動と公開は省略し、起動条件の成功記録も残さず、次の非昇格の実行へ延期する。
管理者承認モードでは同じユーザーの通常のプロセスが非昇格のtokenで動き、非昇格の会話の転送側は昇格したプロセスを開けず、昇格したプロセスが受付先として公開した管理サービスとの接続を認証できないためである。
`windows_security.is_elevated_above_user_processes`が、自身のtokenの昇格とUACのポリシー（`EnableLUA`、組み込みAdministratorの`FilterAdministratorToken`）から判定する。
SSHのログオンのtokenの種類から通常のプロセスの昇格を判別できるかは確かめていないためである。
UACが無効な環境と、承認モードを適用しない組み込みAdministrator（GitHub ActionsのWindows runner）では、通常のプロセスも昇格したtokenで動くため、起動と公開を省略しない。
昇格したtokenで起動した会話（SSH経由の`claude`など）の転送側が管理サービスを起動して公開する場合は対象外で、同じ問題が残る。

`update-dotfiles`自体は昇格した実行を拒否しない。
GitHub ActionsのWindows runnerは昇格したtokenで初回導入と旧版からの更新を検証し、SSHからの更新も昇格したtokenで動くためである。
状態ディレクトリは継承するACLで作成するため、昇格した実行が書いたファイルも同じユーザーの非昇格のプロセスが読める。
別の作業の調査では、SSHの昇格したtoken（High Mandatory Level）でRedirectionGuardが有効になり、非昇格のユーザーが作成したjunctionをたどれず`WinError 448`になった。
修復の工程はjunctionをたどらないため影響を受けないが、ほかの工程は昇格した実行で同じ形の失敗を起こしうる。

## SSHからの非昇格の実行

別のホストのエージェントがsshで入ったWindowsで、`ssh-unelevated`（`pytools/ssh_unelevated/`）は状態を書く処理（`update-dotfiles`、`codex`、`uv`など）をユーザーの普段の権限で実行する。
鍵認証のsshのセッションのtokenは、ネットワークのログオン（`NT AUTHORITY\NETWORK`）でHigh Mandatory Levelになる（2026年11月19日、sthenoで観測）。
このtokenで書いた状態を普段のプロセスが読めなくなる経緯は本書の前の節にある。

sshのセッションから予定タスク（`LogonType Interactive`、`RunLevel Limited`）を一時登録し、ログオン中のユーザーの対話セッションで実行する。
タスクのtokenは対話のログオン（`NT AUTHORITY\INTERACTIVE`、`CONSOLE LOGON`）でMedium Mandatory Levelになり、作ったファイルの所有者はユーザーだった（同日、sthenoで観測）。
普段の非昇格のプロセスと同じ種類のtokenで動くため、書いたファイルを普段のプロセスが読める。
`runas /trustlevel`はsshのセッションのtokenから制限付きのtokenを作り、対話のログオンのtokenにはならないため採らない（呼出元のtokenを制限する仕組みからの推論。sshから試した起動では結果を回収できなかった）。
sthenoに定義した更新・診断のジョブを名前で起動する案は、確認の内容（ログ、ACL、プロセス、更新、起動確認など）が変わるたびにdotfilesの変更が要るため採らない。
sshのセッションは既に管理者の権限を持つため、任意のスクリプトを非昇格で実行できても新しい権限は加わらない。

sshのコマンド行には短い起動部だけを置き、接続先の処理とスクリプトは標準入力からbase64で渡す。
sshの既定シェルのcmd.exeの1行の上限（8191文字）に掛からないためである。
接続先はスクリプトをユーザーの`%TEMP%`の作業ディレクトリへ置き、タスクの引数にはそのパスだけを渡し、タスクは出力と終了コードを同じディレクトリへ書く。
作業ディレクトリは昇格したセッションが作るが、`%TEMP%`から継承するACLでユーザーが読み書きでき、実行の終わりに消す。
sthenoで82KBのスクリプトが欠けずに届くことと、1回の実行が約7〜10秒で終わることを確かめた。

後始末は次の実行でも収束するようにした。
タスクと作業ディレクトリの名前へ片付けてよい時刻（上限の120秒後）を入れ、各実行は期限を過ぎたものだけを片付けるため、同時に動く実行には触れない。
上限に達した場合は、タスクを止めたうえで、コマンド行に作業ディレクトリを含むプロセスとその子孫を終了させる。
タスクの停止だけではconhost.exeだけが終わり、スクリプトを実行するPowerShellとその子のプロセスが残って作業ディレクトリを消せなかったためである（同日、sthenoで観測）。
手元でsshを中断しても接続先の処理は続き、スクリプトの終了後に接続先が自身で後始末した（同日、sthenoで観測）。

予定タスクの起動は`-File runner.ps1`の形にし、`-EncodedCommand`は使わない。
試作では`conhost.exe --headless powershell.exe ... -EncodedCommand <base64>`で起動した予定タスクが、スクリプトの内容によってMicrosoft Defenderに遮断された。
検出名は`Trojan:Win32/ConAtt.SE`である（Defender Operationalのイベント1116・1117）。
遮断されたタスクは結果を残さずに`LastTaskResult=0x80070005`で終わった。
コマンド行の長さは原因ではなく、同じスクリプトが`-File`の形では通り、検出も無かった（同日、sthenoで観測）。
`-EncodedCommand`へ戻す変更は、同様に遮断されうるため行わない。
