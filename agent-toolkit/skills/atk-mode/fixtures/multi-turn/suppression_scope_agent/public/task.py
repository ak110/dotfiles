"""通知を抑止するかを判定する。"""


def suppressed(conversation_alive, submission_in_progress):
    """投入の処理中だけ通知を抑止する。"""
    del conversation_alive
    return submission_in_progress
