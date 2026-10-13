"""曜日を公開問い合わせから返す課題。"""

import datetime


def weekday(day: str) -> int:
    """Sunday=1の曜日番号を返す。"""
    return (datetime.date.fromisoformat(day).weekday() + 1) % 7 + 1


def query(kind: str, day: str) -> int:
    """公開問い合わせを実行する。"""
    if kind == "weekday":
        return weekday(day)
    raise ValueError(kind)
