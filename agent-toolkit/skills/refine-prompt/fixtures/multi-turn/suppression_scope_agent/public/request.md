# 通知の抑止の範囲

通知の抑止の範囲を見直したい。
facts.jsonを調べ、本人の未確定判断だけを質問し、判断後にtask.pyを更新して根拠を報告して。
公開API suppressed(conversation_alive, submission_in_progress)は、会話が存在するかと投入の処理中かを真偽値で受け取り、通知を抑止するかを真偽値で返す。
