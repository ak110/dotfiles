# ユーザーコメント付きAWIの整理

`/atk-mode process-user-comments [入力]`で始める、`## ユーザーコメント`を持つ未処理のAWIを、対話でコメントを踏まえた本文へ整理し直す作業の手順書である。
この作業はユーザーの入力が名指しした時だけ選び、協調モードで動く。
込み入ったコメントを自律モードの処理へそのまま渡すと、質問がUWIで往復しやすい。
本作業は質問を対話で済ませ、処理担当が本文だけで着手できる状態へ整える。

成果物はWIの更新と登録、保留とその解除、ユーザーが取り下げを明示した項目の不採用に限り、`agent-toolkit:atk-mode/playbooks/add-awi-by-user.md`「セッション境界」を適用する。
認識合わせは`implement`・`add-awi-by-user`と共通の`agent-toolkit:atk-mode/playbooks/request-intake.md`、起草と保存は`add-awi-by-user`の手順で行い、本書は対象の抽出、保留、コメントの移動だけを定める。

## 手順

1. 対象を抽出する（読取だけ）。
   `atk wi list --target-repo all --type awi --state inbox --state hold --user-comment yes --pull`を実行する。
   入力がファイル名かリポジトリを指定していれば、その範囲に限る。
   `processing`の項目は別のセッションが処理中のため変えず、`--state processing`で同じ抽出をして件数とファイル名を報告する。
   対象が0件なら、その旨を報告して終える。
   抽出結果のファイル名と`target_repo`を対応付けて作業契約へ記録し、以降の`atk wi`の操作（`show`、`hold`、`edit`、`reject`、`unhold`）には常に`--target-repo <その項目のtarget_repo>`を付ける。
   省略時は現在の作業ツリーのリポジトリだけが対象になるため、別リポジトリの項目にはこの明示が要る。
2. 抽出した対象の全リポジトリを投入先として、`agent-toolkit:atk-mode/playbooks/add-awi-by-user.md`「手順」の1で作業契約を作り、投入作業を記録する。
3. 調査の前に、抽出した`inbox`の項目をリポジトリごとに`atk wi hold --target-repo <リポジトリ> <ファイル名>...`で保留する（`agent-toolkit:atk-mode/references/states-and-dependencies.md`「保留と本文修正」）。
   抽出の後に処理が始まった項目は保留に失敗するため、対象から外して報告し、他の項目と他のリポジトリは続ける（失敗を確かめたら、残りの項目を個別に保留し直す）。
   元から`hold`だった項目は、本作業で保留した項目と区別して作業契約へ記録する。
4. 認識合わせをする。
   各項目の全文を`atk wi show <ファイル名>`で読み、コメントを`agent-toolkit:atk-mode/playbooks/user-utterance.md`「原文の保持と取り込み」「方針の候補の取り込み」で読む。
   是正要求に当たる記入は`agent-toolkit:atk-mode/references/origin-and-approval.md`「ユーザー専用の記入欄と是正要求」に従う。
   そのうえで項目ごとの要求を`agent-toolkit:atk-mode/playbooks/request-intake.md`の手順で受け付けて方針を確定する。
   質問は項目をまたいでまとめてよく、項目のファイル名を問いに添える。
   コメント中の処理担当宛ての質問は、この対話で回答を伝える。
5. 起草する。
   `agent-toolkit:atk-mode/playbooks/add-awi-by-user.md`「起草と保存」の3で、既存AWIの更新として本文を書き直す。
   - コメントの全行を`## ユーザー指摘の逐語引用`へ移し（行ごとの`>`の引用でよい）、この対話で得たユーザーの回答も同じ節へ転記する。
   - コメント中の質問へのエージェントの回答は、逐語引用に混ぜず`## 調査結果と判断根拠`へ書く。
   - 要求は`agent-toolkit:atk-mode/references/awi-body.md`の各節へ反映し、古くなった記述は消す。
   - 分割が要る時は、新しいAWIを同じ手順で投入し、元の項目を残る要求へ書き直す。
   - ユーザーがコメントか回答で取り下げを明示した項目は、`atk wi reject <ファイル名> --note-file <理由の絶対パス>`で終了する。
6. コメントを移して保存する。
   保存の直前に`atk wi show <ファイル名> --section ユーザーコメント`でコメントを読み直し、整理に使った原文と比べる。
   変わっていれば（追記、削除、書き換え）、変わった記入を手順4から扱う。
   一致すれば`atk wi edit <ファイル名> --body-file <原稿の絶対パス> --move-user-comment`で保存し、コメント節を同時に削除する。
   この指定は、保存時点のコメントの全行が逐語引用の節に無い時は保存しないため、読み直しの後の追記も消えない。
   保存に失敗した項目があっても、他の項目は続ける。
7. 保存結果を検収し、項目ごとの終状態を次の区分で決める。
   - 保存に成功し本作業で保留した項目: `atk wi unhold`で戻す。
   - 保存に成功し元から`hold`だった項目: 保留を保ち、更新したことを報告する（コメント節は消えるため、再開の案内は省く）。
   - 回答待ちか保存の失敗が解けないまま終える項目: 保留を保つ。
     コメントが残るため、再開は本作業の再起動か、ユーザーが保留を解いて処理側へ渡すかのどちらかであり、報告にその2つを示す。
   - `atk wi reject`で終了した項目と、手順3で対象から外した項目: 状態をそのまま保つ。
   `unhold`の前に、対象が現在`hold`にあることを`atk wi list`で確かめる。
8. `agent-toolkit:atk-mode/playbooks/add-awi-by-user.md`「起草と保存」の4で投入作業を終える。

## 中止と後始末

ユーザーが中止した時は、`agent-toolkit:atk-mode/playbooks/add-awi-by-user.md`「セッション境界」の中止に従い、本作業で保留した項目のうち現在も`hold`にあるものを`atk wi unhold --target-repo <リポジトリ>`で戻す。
終了した項目と、元から`hold`だった項目はそのまま保つ。
保存済みの更新は戻さず、報告に挙げる。

## 返す報告に含める項目

項目ごとのファイル名と処置（更新、分割と追加したAWI、不採用、変更なし）、保留を保った項目と理由、対象外にした`processing`の項目、AWI本文に記載していない判断。
