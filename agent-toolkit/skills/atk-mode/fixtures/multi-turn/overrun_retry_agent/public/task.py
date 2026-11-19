"""計測値から試験の成否を判定する。"""


def judge_cpu(samples, limit):
    """CPU時間の計測値から成否を返す。"""
    return "fail" if samples[0] > limit else "pass"


def judge_memory(samples, limit):
    """メモリ使用量の計測値から成否を返す。"""
    return "fail" if samples[0] > limit else "pass"
