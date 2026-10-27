# 使っているフォント（試作）

データはフォントの字形から計算しています。いずれも再配布できるライセンスのフォントです。

- Noto Sans CJK / Noto Serif CJK … SIL Open Font License 1.1
- Fredoka（POP の英字など）… SIL Open Font License 1.1
- M PLUS Rounded 1c（POP の日本語）… SIL Open Font License 1.1
- Poppins … SIL Open Font License 1.1
- GNU Unifont / Unifont JP / Unifont Upper（PIXEL）… SIL Open Font License 1.1（GPL 2+ フォント例外付きと二重ライセンス）
- Noto Sans Black / Noto Sans CJK Black / 各文字の Noto Sans ○○ Black（OUTLINE の元）… SIL Open Font License 1.1
- DejaVu Sans / DejaVu Serif … Bitstream Vera / DejaVu License
- Loma (TLWG) / FreeSans / FreeSerif (GNU FreeFont) … GPL（フォント例外付き）

POP は Fredoka → M PLUS Rounded 1c → Noto Sans CJK Black → その他 の順で、最初に字形があるフォントを使います。

PIXEL は Unifont → Unifont JP → Unifont Upper → Noto Sans CJK → その他 の順です。

BLACK は Noto Sans Black → Noto Sans CJK Black → アラビア文字・タイ文字・デーヴァナーガリーなど各文字の Noto Sans Black → その他 の順です。

強さ（面積・バランス・先端・タイプ）は字形から計算するので、同じ文字でも字体ごとに変わります。

OUTLINE・ITALIC はフォントを増やさず、形を加工して作っています（どの言語でも使えます）。
- OUTLINE：BLACK の形のふちだけを残す（中が空洞なので軽い）
- ITALIC：SANS の形を 13 度傾ける
