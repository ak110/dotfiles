# 超過の判定の見直し

CIの計測でCPU時間とメモリー使用量の超過による失敗が続いていて、公開の工程が進まない。
超過の判定を見直したい。
facts.jsonを調べ、本人の未確定判断だけを質問し、判断後にtask.pyを更新して根拠を報告して。
公開APIはjudge_cpu(samples, limit)とjudge_memory(samples, limit)で、同じ試験を計測した順の値の配列を受け取り、failかpassを返す。
再実行で確かめる場合は、全ての計測が上限を超えた時だけfailにする。
