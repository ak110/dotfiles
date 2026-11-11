# 権限拒否への対応

本書はツール呼び出しの拒否と、事前判定の判定不能への対応を定める。
最初に「共通判断」で実行した主体と診断を確認し、該当する節だけを読む。
Claude Codeのauto modeのカスタムルールを追加・編集する時は、`agent-toolkit:user-confirmation-and-report/references/auto-mode.md`に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 共通判断

拒否された対象・操作、診断本文、実行した主体と拒否した主体を保持する。
診断から、agents_serverのwriter境界、Codexホストの権限、Claude Codeのpermissionsまたはauto mode classifierを区別し、下表の詳細を選ぶ。
拒否された範囲の保護を尊重し、別手段・別担当・設定変更によって迂回しない。
委譲先は対象・操作・診断と必要な認可や入力を委譲元へ報告し、委譲元が確認手順を引き継ぐ。
作業依頼の認可だけでホストやサービスの拒否を解除しない。

| 診断と拒否主体 | 読む節 |
| --- | --- |
| agents_serverのwriter境界 | 「writer境界・Codexホストの拒否」 |
| Codexホストの権限 | 「writer境界・Codexホストの拒否」 |
| Claude Codeのpermissions・auto mode classifier | 「拒否の確認手順」。classifierの偽陽性を判断する場合は「既知の誤拒否パターンと対応」「偽陽性と判断できる拒否への対応」も読む |
| classifierの`gave no verdict (error)`など、通知本文が一時的な失敗として再試行を許すもの | 「一時的な判定不能」の手順2へ進む |
| classifierの`gave no verdict (it skipped this action)`など、通知本文がhard failureとして再試行を禁じるもの | 「一時的な判定不能」の手順3へ進む |
| 拒否主体が未確定 | 診断本文、呼出先、起動routeなど、主体を区別するために必要な範囲だけを取得する。偽陽性と推定して保護を解除しない |

アクセス制御、認証、公開範囲などのセキュリティ設定を変更する操作が拒否された場合は、変更候補の作成、現在の実効値と候補の比較、候補の適用のうち、どの段階が拒否されたのかを特定する（1つのツール呼び出しに複数の操作が含まれていた場合は、候補の作成が拒否されたものとみなす）。
候補の作成が拒否された場合は、別の手段で同様の候補を勝手に生成せず、比較が未実施であることを明記した上で、拒否された操作の承認をユーザーへ確認する（委譲先の場合は委譲元へ報告する）。
セキュリティ設定の変更は、必ず承認を得てから実施する。
比較手段が機能しない場合は、実効値が維持されると断定せず、比較できた項目と未確認の範囲を分けて報告する。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| writer境界・Codexホストの拒否に関する判断・作業の前 | `permission-denial-host.md` |
| 一時的な判定不能に関する判断・作業の前 | `permission-denial-transient.md` |
| 拒否の確認手順・既知の誤拒否パターンと対応・偽陽性と判断できる拒否への対応に関する判断・作業の前 | `permission-denial-classifier.md` |
