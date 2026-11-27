export default {
  "suppress_repeats": true,
  "show_total_with_delta": true,
  "omit_defaults": true,
  "full_guidance_once": true,
  "search_guidance": "{operation}の前に、スキル`{skill}`を起動してください。",
  "wait_improvement_guidance": "`気付いた改善点:`は送達成功記録と比べ、未送達・新しい観測を「受領後の扱い」に従って転記してください。",
  "send_guidance": "送信済み。再送・再掲・確認・要約は不要。新しい報告はsend_to_userで送る。新情報がなく本文が必須なら「…」。",
  "send_repeat": "送信済み。再送・再掲は不要。",
  "acceptance": {
    "search": {
      "max_chars": 120,
      "includes": [
        "agent-basics:search",
        "起動してください"
      ],
      "excludes": []
    },
    "search_repeat": {
      "max_chars": 0,
      "includes": [],
      "excludes": []
    },
    "wait_improvement": {
      "max_chars": 70,
      "includes": [
        "気付いた改善点",
        "受領後の扱い"
      ],
      "excludes": [
        "逐語"
      ]
    },
    "send_first": {
      "max_chars": 70,
      "includes": [
        "送信済み",
        "send_to_user",
        "…"
      ],
      "excludes": []
    },
    "send_repeat": {
      "max_chars": 25,
      "includes": [
        "送信済み",
        "再送・再掲は不要"
      ],
      "excludes": [
        "send_to_user"
      ]
    },
    "wi_progress_first": {
      "max_chars": 150,
      "includes": [
        "処理可能な新着WI",
        "計1件",
        "source: implement"
      ],
      "excludes": [
        "merge_branch: なし"
      ]
    },
    "wi_progress_repeat": {
      "max_chars": 30,
      "includes": [
        "処理可能な新着WI",
        "計1件"
      ],
      "excludes": [
        "source:"
      ]
    }
  }
};
