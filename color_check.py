#!/usr/bin/env python3
"""
配色对比度校验 —— WCAG 2.1 AA

前端把类别色用作标签文字色（.card-tag），所以每个色值必须在自己的
底色上达到 4.5:1（正文级），否则小字看不清。

改 CATEGORIES 里的颜色后跑一下这个脚本，全绿再提交:
    py -3.9 color_check.py
"""

import sys

from classifier import CATEGORIES, CATEGORY_ORDER

# 前端实际的底色（与 docs/index.html 的 --card 一致）
LIGHT_BG = "#FFFFFF"
DARK_BG = "#1B1E24"

# WCAG AA: 正文 4.5:1，大字 3:1。标签是 10px 小字，按正文要求。
AA_NORMAL = 4.5


def _to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"无法解析颜色: {hex_color}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _luminance(hex_color: str) -> float:
    """WCAG 相对亮度"""
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _to_rgb(hex_color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg: str, bg: str) -> float:
    """对比度 (1.0 ~ 21.0)"""
    l1, l2 = _luminance(fg), _luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    failures = []

    print("=" * 72)
    print("  配色对比度校验 (WCAG 2.1 AA, 阈值 %.1f:1)" % AA_NORMAL)
    print("=" * 72)
    print(f"  {'类别':<10} {'浅色值':<10} {'对比度':>8}   {'深色值':<10} {'对比度':>8}")
    print("-" * 72)

    for key in CATEGORY_ORDER:
        meta = CATEGORIES[key]
        light, dark = meta["color"], meta["color_dk"]

        c_light = contrast(light, LIGHT_BG)
        c_dark = contrast(dark, DARK_BG)

        ok_light = c_light >= AA_NORMAL
        ok_dark = c_dark >= AA_NORMAL
        mark_l = "✅" if ok_light else "❌"
        mark_d = "✅" if ok_dark else "❌"

        print(f"  {meta['label']:<10} {light:<10} {c_light:>6.2f}:1 {mark_l}  "
              f"{dark:<10} {c_dark:>6.2f}:1 {mark_d}")

        if not ok_light:
            failures.append((meta["label"], "浅色", light, LIGHT_BG, c_light))
        if not ok_dark:
            failures.append((meta["label"], "深色", dark, DARK_BG, c_dark))

    print("=" * 72)
    if failures:
        print(f"\n❌ {len(failures)} 项未通过:\n")
        for label, mode, fg, bg, ratio in failures:
            need = AA_NORMAL
            print(f"  {label} [{mode}] {fg} on {bg} = {ratio:.2f}:1"
                  f"  (需要 >= {need}:1)")
        print("\n调亮（深色模式）或调暗（浅色模式）后重跑。")
        return 1

    print("\n✅ 全部通过 —— 两种模式下都是 AA 级可读。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
