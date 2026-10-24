import io
import os
import re
import sys
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding="utf-8")

BUILD_DIR = Path(r"C:\Users\Administrator\baiduime\_build")
OUT_DIR = Path(r"C:\Users\Administrator\baiduime")
ORIG_BDS = BUILD_DIR / "orig_a5b04952.bds"


def get_font(size: int, bold: bool = True):
    fonts_dir = Path(r"C:\Windows\Fonts")
    candidates = ["msyhbd.ttc", "seguisb.ttf", "arialbd.ttf", "msyh.ttc"] if bold else ["msyh.ttc", "segoeui.ttf", "arial.ttf"]
    for name in candidates:
        p = fonts_dir / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def draw_centered_text(draw: ImageDraw.ImageDraw, cx, cy, text, font, fill):
    if not text:
        return
    if len(text) == 1 and text in "，。；：？！、":
        tmp = Image.new("L", (120, 120), 0)
        td = ImageDraw.Draw(tmp)
        td.text((20, 20), text, font=font, fill=255)
        ib = tmp.getbbox()
        if ib:
            iw, ih = ib[2] - ib[0], ib[3] - ib[1]
            draw.text((cx - iw / 2 - (ib[0] - 20), cy - ih / 2 - (ib[1] - 20)), text, font=font, fill=fill)
            return
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, font=font, fill=fill)


def render_keycap_pair(w: int, h: int, center: str, up: str, down: str, kind: str, small: bool, is_land: bool, is_dark: bool = False):
    """
    Render (normal_rgba, pressed_rgba) keycap tile of size (w, h).
    """
    if not is_dark:
        if kind == "red":
            nm_fill, hl_fill = (217, 83, 79, 255), (192, 66, 62, 255)
            outline, shadow = (196, 67, 63, 255), (168, 52, 48, 255)
            mc, uc, dc = (255, 255, 255, 255), (255, 228, 226, 255), (255, 228, 226, 255)
        elif kind == "esc":
            nm_fill, hl_fill = (238, 214, 212, 255), (222, 192, 190, 255)
            outline, shadow = (206, 178, 176, 255), (186, 160, 158, 255)
            mc, uc, dc = (145, 38, 35, 255), (145, 38, 35, 255), (118, 125, 132, 255)
        elif kind == "nav":
            nm_fill, hl_fill = (74, 107, 83, 255), (54, 82, 63, 255)
            outline, shadow = (58, 88, 66, 255), (42, 66, 48, 255)
            mc, uc, dc = (255, 255, 255, 255), (216, 234, 222, 255), (216, 234, 222, 255)
        elif kind == "island":
            nm_fill, hl_fill = (222, 234, 225, 255), (198, 216, 204, 255)
            outline, shadow = (176, 198, 182, 255), (164, 184, 170, 255)
            mc, uc, dc = (30, 58, 40, 255), (74, 107, 83, 255), (96, 116, 102, 255)
        elif kind == "num":
            nm_fill, hl_fill = (255, 255, 255, 255), (232, 236, 240, 255)
            outline, shadow = (204, 208, 204, 255), (188, 192, 188, 255)
            mc, uc, dc = (26, 29, 32, 255), (205, 72, 68, 255), (118, 125, 132, 255)
        elif kind == "gray":
            nm_fill, hl_fill = (221, 224, 228, 255), (198, 204, 210, 255)
            outline, shadow = (196, 200, 206, 255), (178, 184, 190, 255)
            mc, uc, dc = (32, 36, 40, 255), (74, 107, 83, 255), (108, 115, 122, 255)
        else:
            nm_fill, hl_fill = (255, 255, 255, 255), (232, 236, 240, 255)
            outline, shadow = (204, 208, 204, 255), (188, 192, 188, 255)
            mc, uc, dc = (26, 29, 32, 255), (74, 107, 83, 255), (118, 125, 132, 255)
    else:
        if kind == "red":
            nm_fill, hl_fill = (224, 76, 76, 255), (190, 58, 58, 255)
            outline, shadow = (180, 56, 56, 255), (20, 20, 22, 255)
            mc, uc, dc = (255, 255, 255, 255), (255, 220, 220, 255), (255, 220, 220, 255)
        elif kind == "nav":
            nm_fill, hl_fill = (42, 102, 72, 255), (56, 128, 92, 255)
            outline, shadow = (64, 138, 100, 255), (18, 24, 20, 255)
            mc, uc, dc = (255, 255, 255, 255), (190, 235, 210, 255), (190, 235, 210, 255)
        elif kind == "island":
            nm_fill, hl_fill = (44, 62, 54, 255), (60, 84, 72, 255)
            outline, shadow = (72, 104, 88, 255), (18, 22, 20, 255)
            mc, uc, dc = (210, 240, 224, 255), (144, 200, 172, 255), (144, 200, 172, 255)
        elif kind == "num":
            nm_fill, hl_fill = (52, 56, 62, 255), (72, 78, 86, 255)
            outline, shadow = (76, 82, 90, 255), (20, 22, 24, 255)
            mc, uc, dc = (238, 242, 246, 255), (240, 110, 106, 255), (120, 196, 152, 255)
        elif kind == "gray":
            nm_fill, hl_fill = (44, 46, 50, 255), (64, 68, 74, 255)
            outline, shadow = (66, 70, 76, 255), (18, 18, 20, 255)
            mc, uc, dc = (220, 224, 230, 255), (150, 156, 164, 255), (120, 196, 152, 255)
        else:
            nm_fill, hl_fill = (58, 60, 64, 255), (80, 84, 90, 255)
            outline, shadow = (78, 82, 88, 255), (20, 20, 22, 255)
            mc, uc, dc = (240, 242, 245, 255), (160, 166, 174, 255), (120, 196, 152, 255)

    # Physical aspect-ratio compensation for Xiaomi Pad 8 Pro (3200x2136 land / 2136x3200 port)
    # Landscape key area: logical 1920x675 -> physical 3200x616 (sx=1.6667, sy=0.9126)
    # Portrait key area:  logical 1125x530 -> physical 2136x550 (sx=1.8987, sy=1.0377)
    if is_land:
        rw = max(24, int(round(w * 1.25)))
        rh = max(24, int(round(h * 0.6844)))
    else:
        rw = max(24, int(round(w * 1.424)))
        rh = max(24, int(round(h * 0.7783)))

    f_main = get_font(28 if is_land else 24, True)
    f_main_sm = get_font(19 if is_land else 16, True)
    f_sub = get_font(12 if is_land else 11, True)

    out_pair = []
    for is_hl, fill_col in [(False, nm_fill), (True, hl_fill)]:
        im = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        rad = 11 if is_land else 10
        d.rounded_rectangle([3, 5, rw - 3, rh - 2], radius=rad, fill=shadow)
        y_top = 3 if is_hl else 2
        d.rounded_rectangle([3, y_top, rw - 3, rh - 4], radius=rad, fill=fill_col, outline=outline, width=2)

        cx = rw / 2
        cy = rh / 2 + (1 if is_hl else 0)
        up_y = (15 if is_land else 13) + (1 if is_hl else 0)
        dn_y = rh - (15 if is_land else 14) + (1 if is_hl else 0)

        if up and not down and len(center) == 1 and center.isalpha():
            f_sub_single = get_font(14 if is_land else 12, True)
            draw_centered_text(d, cx, up_y + 2, up, f_sub_single, uc)
            draw_centered_text(d, cx, int(rh * 0.62) + (1 if is_hl else 0), center, f_main, mc)
        else:
            if up:
                draw_centered_text(d, cx, up_y, up, f_sub, uc)
            if down:
                draw_centered_text(d, cx, dn_y, down, f_sub, dc)
            use_f = f_main_sm if small else f_main
            draw_centered_text(d, cx, cy - 1, center, use_f, mc)
        out_pair.append(im)

    return out_pair[0], out_pair[1]


def get_landscape_key_defs():
    """
    Returns list of 58 key dicts for Landscape 1920x680 Split 65% Physical Keyboard (v2):
    - Narrow Left Thumb Half: x = 14 .. 512 (max 26.6% of screen width, 60-65mm)
    - Narrow Right Thumb Half: x = 1370 .. 1906 (max 27.9% of screen width, 62-68mm)
    - Center Open Ergonomic Gap: x = 512 .. 1370 (44.7% open space)
    - Authentic +18u (+0.25u) interlocking physical row stagger on both halves
    - Zero redundancy between Row 1 (Numbers + ANSI Shift symbols) and Row 2 (QWERTY + math/currency/brackets)
    """
    rh = 135
    y0, y1, y2, y3, y4 = 0, 135, 270, 405, 540
    defs = []

    # ==================== LEFT SPLIT HALF (x = 14 .. 512) ====================
    # Row 1 (y=0, x=14..458): Esc, 1, 2, 3, 4, 5
    r1_l = [
        (74, "Esc", "`", "~", "esc", True, "F40", "`", "~"),
        (74, "1", "!", "！", "num", False, "1", "!", "！"),
        (74, "2", "@", "²", "num", False, "2", "@", "²"),
        (74, "3", "#", "³", "num", False, "3", "#", "³"),
        (74, "4", "¥", "$", "num", False, "4", "￥", "$"),
        (74, "5", "%", "‰", "num", False, "5", "%", "‰"),
    ]
    cx = 14
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r1_l:
        defs.append({"rect": (cx, y0, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 2 (y=135, x=14..476, staggered +18u): Tab(92), Q..T(74)
    r2_l = [
        (92, "Tab", "缩进", "反缩", "gray", True, "F41", "F41", "F48"),
        (74, "Q", "§", "", "light", False, "q", "§", "§"),
        (74, "W", "€", "", "light", False, "w", "€", "€"),
        (74, "E", "£", "", "light", False, "e", "£", "£"),
        (74, "R", "®", "", "light", False, "r", "®", "®"),
        (74, "T", "™", "", "light", False, "t", "™", "™"),
    ]
    cx = 14
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r2_l:
        defs.append({"rect": (cx, y1, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 3 (y=270, x=14..494, staggered +18u): Caps(110), A..G(74)
    r3_l = [
        (110, "Caps", "大写", "锁定", "gray", True, "F11", "F11", "F1"),
        (74, "A", "全选", "", "light", False, "a", "F47", "F47"),
        (74, "S", "°", "", "light", False, "s", "°", "°"),
        (74, "D", "℃", "", "light", False, "d", "℃", "℃"),
        (74, "F", "「", "", "light", False, "f", "「", "「"),
        (74, "G", "」", "", "light", False, "g", "」", "」"),
    ]
    cx = 14
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r3_l:
        defs.append({"rect": (cx, y2, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 4 (y=405, x=14..512, staggered +18u): Shift(128), Z..B(74)
    r4_l = [
        (128, "Shift", "上档", "符号", "gray", True, "F11", "F1", "'"),
        (74, "Z", "撤销", "", "light", False, "z", "F67", "F67"),
        (74, "X", "剪切", "", "light", False, "x", "F44", "F44"),
        (74, "C", "复制", "", "light", False, "c", "F45", "F45"),
        (74, "V", "粘贴", "", "light", False, "v", "F46", "F46"),
        (74, "B", "重做", "", "light", False, "b", "F66", "F66"),
    ]
    cx = 14
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r4_l:
        defs.append({"rect": (cx, y3, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 5 (y=540, x=14..512): 符SYM(84), 中/英(84), 剪贴板(84), SPACE 左空格(246)
    defs.extend([
        {"rect": (14, y4, 82, rh), "c": "符SYM", "u": "表情", "d": "更多", "kind": "gray", "sm": True, "CENTER": "F1", "UP": "F7", "DOWN": "F6"},
        {"rect": (98, y4, 82, rh), "c": "中/英", "u": "切换", "d": "EN", "kind": "gray", "sm": True, "CENTER": "F16", "UP": "F84"},
        {"rect": (182, y4, 82, rh), "c": "剪贴板", "u": "清空", "d": "词库", "kind": "island", "sm": True, "CENTER": "F76", "UP": "F48", "DOWN": "F77"},
        {"rect": (266, y4, 244, rh), "c": "SPACE 左空格", "u": "", "d": "← 滑动移光标 →", "kind": "light", "sm": True, "CENTER": "F38", "UP": "F77", "LEFT": "F51", "RIGHT": "F52", "HOLD": "F72"},
    ])

    # ==================== RIGHT SPLIT HALF (x = 1370 .. 1906) ====================
    # Row 1 (y=0, x=1370..1906): 6..0(74), -(74), 退格(92)
    r1_r = [
        (74, "6", "^", "……", "num", False, "6", "^", "……"),
        (74, "7", "&", "｜", "num", False, "7", "&", "|"),
        (74, "8", "*", "※", "num", False, "8", "*", "※"),
        (74, "9", "(", "（", "num", False, "9", "(", "（"),
        (74, "0", ")", "）", "num", False, "0", ")", "）"),
        (74, "-", "_", "——", "num", False, "-", "_", "——"),
        (92, "退格", "清空", "Back", "gray", True, "F36", "F48", "F37"),
    ]
    cx = 1370
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r1_r:
        defs.append({"rect": (cx, y0, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 2 (y=135, x=1388..1906, staggered +18u): Y..P(74), [(74), ](74)
    r2_r = [
        (74, "Y", "×", "", "light", False, "y", "×", "×"),
        (74, "U", "÷", "", "light", False, "u", "÷", "÷"),
        (74, "I", "≠", "", "light", False, "i", "≠", "≠"),
        (74, "O", "≈", "", "light", False, "o", "≈", "≈"),
        (74, "P", "√", "", "light", False, "p", "√", "√"),
        (74, "[", "{", "【", "light", False, "[", "{", "【"),
        (74, "]", "}", "】", "light", False, "]", "}", "】"),
    ]
    cx = 1388
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r2_r:
        defs.append({"rect": (cx, y1, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 3 (y=270, x=1406..1906, staggered +18u): H..L(74), ；(64), “(64), Enter(76)
    r3_r = [
        (74, "H", "『", "", "light", False, "h", "『", "『"),
        (74, "J", "』", "", "light", False, "j", "』", "』"),
        (74, "K", "《", "", "light", False, "k", "《", "《"),
        (74, "L", "》", "", "light", False, "l", "》", "》"),
        (64, "；", ":", ";", "light", False, "；", "：", ";"),
        (64, "“", "\"", "”", "light", False, "“", "\"", "”"),
        (76, "Enter", "换行", "回车", "red", True, "F39", "F39", "F76"),
    ]
    cx = 1406
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r3_r:
        defs.append({"rect": (cx, y2, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 4 (y=405, x=1424..1906, staggered +18u): N(74), M(74), ，(66), 。(66), /(66), ↑(68), Del(68)
    r4_r = [
        (74, "N", "Home", "", "light", False, "n", "F42", "F42"),
        (74, "M", "End", "", "light", False, "m", "F43", "F43"),
        (66, "，", "<", ",", "light", False, "，", "<", ","),
        (66, "。", ">", ".", "light", False, "。", ">", "."),
        (66, "/", "？", "、", "light", False, "/", "？", "、"),
        (68, "↑", "PgUp", "上移", "nav", False, "F49", "F42", "F49"),
        (68, "Del", "清空", "前删", "island", True, "F37", "F48", "F8"),
    ]
    cx = 1424
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r4_r:
        defs.append({"rect": (cx, y3, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 5 (y=540, x=1424..1906): SPACE 右空格(212), =(66), ←(68), ↓(68), →(68)
    defs.extend([
        {"rect": (1424, y4, 210, rh), "c": "SPACE 右空格", "u": "", "d": "← 滑动移光标 →", "kind": "light", "sm": True, "CENTER": "F38", "UP": "F77", "LEFT": "F51", "RIGHT": "F52", "HOLD": "F72"},
        {"rect": (1636, y4, 64, rh), "c": "=", "u": "+", "d": "±", "kind": "light", "sm": False, "CENTER": "=", "UP": "+", "DOWN": "±"},
        {"rect": (1702, y4, 66, rh), "c": "←", "u": "Home", "d": "左移", "kind": "nav", "sm": False, "CENTER": "F51", "UP": "F42", "LEFT": "F51"},
        {"rect": (1770, y4, 66, rh), "c": "↓", "u": "PgDn", "d": "下移", "kind": "nav", "sm": False, "CENTER": "F50", "DOWN": "F50"},
        {"rect": (1838, y4, 66, rh), "c": "→", "u": "End", "d": "右移", "kind": "nav", "sm": False, "CENTER": "F52", "UP": "F43", "RIGHT": "F52"},
    ])
    return defs


def get_portrait_key_defs():
    """
    Returns list of 62 key dicts for Portrait 1125x536 Authentic 65% Physical Keyboard (v2):
    - 5 staggered rows matching ANSI 65% physical layout
    - Dedicated Esc, 1..0, -, =, 退格, Tab, [ ], 、, Caps, ；, “, Enter, Shift, ，, 。, /, ↑, Del, ←, ↓, →
    - Zero redundancy between Row 1 and Row 2
    """
    rh = 106
    y0, y1, y2, y3, y4 = 0, 106, 212, 318, 424
    defs = []

    # Row 1 (14 keys, x=8..1117): Esc(74), 1..0(10*77), -(74), =(74), 退格(117)
    r1 = [
        (74, "Esc", "`", "~", "esc", True, "F40", "`", "~"),
        (77, "1", "!", "！", "num", False, "1", "!", "！"),
        (77, "2", "@", "²", "num", False, "2", "@", "²"),
        (77, "3", "#", "³", "num", False, "3", "#", "³"),
        (77, "4", "¥", "$", "num", False, "4", "￥", "$"),
        (77, "5", "%", "‰", "num", False, "5", "%", "‰"),
        (77, "6", "^", "……", "num", False, "6", "^", "……"),
        (77, "7", "&", "｜", "num", False, "7", "&", "|"),
        (77, "8", "*", "※", "num", False, "8", "*", "※"),
        (77, "9", "(", "（", "num", False, "9", "(", "（"),
        (77, "0", ")", "）", "num", False, "0", ")", "）"),
        (74, "-", "_", "——", "num", False, "-", "_", "——"),
        (74, "=", "+", "±", "num", False, "=", "+", "±"),
        (117, "退格", "清空", "Back", "gray", True, "F36", "F48", "F37"),
    ]
    cx = 8
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r1:
        defs.append({"rect": (cx, y0, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 2 (14 keys, x=8..1117): Tab(95), Q..P(10*78), [(78), ](78), 、(78)
    r2 = [
        (95, "Tab", "缩进", "反缩", "gray", True, "F41", "F41", "F48"),
        (78, "Q", "§", "", "light", False, "q", "§", "§"),
        (78, "W", "€", "", "light", False, "w", "€", "€"),
        (78, "E", "£", "", "light", False, "e", "£", "£"),
        (78, "R", "®", "", "light", False, "r", "®", "®"),
        (78, "T", "™", "", "light", False, "t", "™", "™"),
        (78, "Y", "×", "", "light", False, "y", "×", "×"),
        (78, "U", "÷", "", "light", False, "u", "÷", "÷"),
        (78, "I", "≠", "", "light", False, "i", "≠", "≠"),
        (78, "O", "≈", "", "light", False, "o", "≈", "≈"),
        (78, "P", "√", "", "light", False, "p", "√", "√"),
        (78, "[", "{", "【", "light", False, "[", "{", "【"),
        (78, "]", "}", "】", "light", False, "]", "}", "】"),
        (78, "、", "|", "\\", "light", False, "、", "|", "\\"),
    ]
    cx = 8
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r2:
        defs.append({"rect": (cx, y1, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 3 (13 keys, x=8..1117): Caps(112), A..L(9*78), ；(78), “(78), Enter(139)
    r3 = [
        (112, "Caps", "大写", "锁定", "gray", True, "F11", "F11", "F1"),
        (78, "A", "全选", "", "light", False, "a", "F47", "F47"),
        (78, "S", "°", "", "light", False, "s", "°", "°"),
        (78, "D", "℃", "", "light", False, "d", "℃", "℃"),
        (78, "F", "「", "", "light", False, "f", "「", "「"),
        (78, "G", "」", "", "light", False, "g", "」", "」"),
        (78, "H", "『", "", "light", False, "h", "『", "『"),
        (78, "J", "』", "", "light", False, "j", "』", "』"),
        (78, "K", "《", "", "light", False, "k", "《", "《"),
        (78, "L", "》", "", "light", False, "l", "》", "》"),
        (78, "；", ":", ";", "light", False, "；", "：", ";"),
        (78, "“", "\"", "”", "light", False, "“", "\"", "”"),
        (139, "Enter", "换行", "回车", "red", True, "F39", "F39", "F76"),
    ]
    cx = 8
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r3:
        defs.append({"rect": (cx, y2, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 4 (13 keys, x=8..1117): Shift(129), Z..M(7*78), ，(78), 。(78), /(78), ↑(100), Del(100)
    r4 = [
        (129, "Shift", "上档", "符号", "gray", True, "F11", "F1", "'"),
        (78, "Z", "撤销", "", "light", False, "z", "F67", "F67"),
        (78, "X", "剪切", "", "light", False, "x", "F44", "F44"),
        (78, "C", "复制", "", "light", False, "c", "F45", "F45"),
        (78, "V", "粘贴", "", "light", False, "v", "F46", "F46"),
        (78, "B", "重做", "", "light", False, "b", "F66", "F66"),
        (78, "N", "Home", "", "light", False, "n", "F42", "F42"),
        (78, "M", "End", "", "light", False, "m", "F43", "F43"),
        (78, "，", "<", ",", "light", False, "，", "<", ","),
        (78, "。", ">", ".", "light", False, "。", ">", "."),
        (78, "/", "？", "?", "light", False, "/", "？", "?"),
        (100, "↑", "PgUp", "上移", "nav", False, "F49", "F42", "F49"),
        (100, "Del", "清空", "前删", "island", True, "F37", "F48", "F37"),
    ]
    cx = 8
    for w, c, u, d, kind, sm, act_c, act_u, act_d in r4:
        defs.append({"rect": (cx, y3, w - 2, rh), "c": c, "u": u, "d": d, "kind": kind, "sm": sm, "CENTER": act_c, "UP": act_u, "DOWN": act_d})
        cx += w

    # Row 5 (8 keys, x=8..1117): 符SYM(95), 中/英(95), 剪贴板(95), SPACE 空格(424), 收起(100), ←(100), ↓(100), →(100)
    defs.extend([
        {"rect": (8, y4, 93, rh), "c": "符SYM", "u": "表情", "d": "更多", "kind": "gray", "sm": True, "CENTER": "F1", "UP": "F7", "DOWN": "F6"},
        {"rect": (103, y4, 93, rh), "c": "中/英", "u": "切换", "d": "EN", "kind": "gray", "sm": True, "CENTER": "F16", "UP": "F84"},
        {"rect": (198, y4, 93, rh), "c": "剪贴板", "u": "清空", "d": "粘贴", "kind": "island", "sm": True, "CENTER": "F76", "UP": "F48", "DOWN": "F46"},
        {"rect": (293, y4, 422, rh), "c": "SPACE 空格", "u": "", "d": "← 滑动移光标 →", "kind": "light", "sm": True, "CENTER": "F38", "UP": "F77", "LEFT": "F51", "RIGHT": "F52", "HOLD": "F72"},
        {"rect": (717, y4, 98, rh), "c": "收起", "u": "菜单", "d": "Hide", "kind": "gray", "sm": True, "CENTER": "F8", "UP": "F31", "DOWN": "F8"},
        {"rect": (817, y4, 98, rh), "c": "←", "u": "Home", "d": "左移", "kind": "nav", "sm": False, "CENTER": "F51", "UP": "F42", "LEFT": "F51"},
        {"rect": (917, y4, 98, rh), "c": "↓", "u": "PgDn", "d": "下移", "kind": "nav", "sm": False, "CENTER": "F50", "DOWN": "F50"},
        {"rect": (1017, y4, 98, rh), "c": "→", "u": "End", "d": "右移", "kind": "nav", "sm": False, "CENTER": "F52", "UP": "F43", "RIGHT": "F52"},
    ])
    return defs


def build_pad_layout_ini(key_defs, style_map, is_land: bool, is_en: bool = False):
    lines = [
        "[INPUT]",
        "",
        "[CAND]",
        "",
        "[PANEL]",
        "NO_BLUR=1",
        f"KEY_NUM={len(key_defs)}",
        "TIP_NUM=0",
        "",
        "[MORE]",
        "",
        "[HINT]",
        "LAYOUT_NAME=hint2",
        "",
    ]
    for idx, kd in enumerate(key_defs, start=1):
        x, y, w, h = kd["rect"]
        sig = (w, h, kd["c"], kd["u"], kd["d"], kd["kind"], kd["sm"], is_land)
        st_id = style_map[sig]
        center_val = kd["CENTER"]
        if is_en and center_val in ("，", "。"):
            center_val = "," if center_val == "，" else "."
        lines.extend([
            f"[KEY{idx}]",
            f"BACK_STYLE={st_id}",
            "FORE_STYLE=999",
            "POS_TYPE=1",
            f"VIEW_RECT={x},{y},{w},{h}",
            f"CENTER={center_val}",
        ])
        for dir_k in ("UP", "DOWN", "LEFT", "RIGHT", "HOLD"):
            if dir_k in kd and kd[dir_k]:
                lines.append(f"{dir_k}={kd[dir_k]}")
        lines.extend([
            "BACK_ANIM_STYLE=501",
            "FORE_ANIM_STYLE=501",
            "",
        ])
    lines.append("[SCAND]\r\n")
    return "\r\n".join(lines)


def build_pad_theme_assets(orig_zf: zipfile.ZipFile, is_dark: bool = False):
    land_defs = get_landscape_key_defs()
    port_defs = get_portrait_key_defs()

    # Collect unique visual keycap signatures
    unique_sigs = []
    seen = set()
    for is_land, klist in [(True, land_defs), (False, port_defs)]:
        for kd in klist:
            w, h = kd["rect"][2], kd["rect"][3]
            sig = (w, h, kd["c"], kd["u"], kd["d"], kd["kind"], kd["sm"], is_land)
            if sig not in seen:
                seen.add(sig)
                unique_sigs.append(sig)

    orig_anjian = Image.open(io.BytesIO(orig_zf.read("res/anjian.png"))).convert("RGBA")
    ATLAS_W = 2400
    cur_x = 0
    cur_y = 164
    row_h = 0

    style_map = {}
    placed_tiles = []
    extra_css = [
        "[STYLE999]\r\nSHOW= \r\nNM_COLOR=00000000\r\nHL_COLOR=00000000\r\nFONT_SIZE=1"
    ]

    next_tile_id = 17
    next_style_id = 3101

    for sig in unique_sigs:
        w, h, c, u, d, kind, sm, is_land = sig
        nm_im, hl_im = render_keycap_pair(w, h, c, u, d, kind, sm, is_land, is_dark=is_dark)
        for im in (nm_im, hl_im):
            tw, th = im.size
            if cur_x + tw + 4 > ATLAS_W:
                cur_x = 0
                cur_y += row_h + 4
                row_h = 0
            placed_tiles.append((next_tile_id, cur_x, cur_y, tw, th, im))
            cur_x += tw + 4
            row_h = max(row_h, th)
            next_tile_id += 1

        nm_t = next_tile_id - 2
        hl_t = next_tile_id - 1
        st_id = next_style_id
        next_style_id += 1
        style_map[sig] = st_id
        extra_css.append(f"[STYLE{st_id}]\r\nNM_IMG=anjian,{nm_t}\r\nHL_IMG=anjian,{hl_t}")

    total_h = cur_y + row_h + 4
    atlas = Image.new("RGBA", (ATLAS_W, total_h), (0, 0, 0, 0))
    atlas.paste(orig_anjian, (0, 0))

    til_lines = [
        "[GLOBAL]",
        "USE_ALPHA=1",
        f"TILE_NUM={next_tile_id - 1}",
        "",
    ]
    orig_rects = [
        (0, 0, 110, 120, 28, 28),
        (110, 0, 110, 120, 28, 28),
        (220, 0, 110, 120, 28, 28),
        (330, 0, 110, 120, 28, 28),
        (440, 0, 110, 120, 28, 28),
        (550, 0, 110, 120, 28, 28),
        (660, 0, 110, 120, 28, 28),
        (770, 0, 110, 120, 28, 28),
        (880, 0, 110, 120, 28, 28),
        (990, 0, 110, 120, 28, 28),
        (1100, 0, 110, 120, 28, 28),
        (1210, 0, 110, 120, 28, 28),
        (1320, 0, 110, 60, 28, 4),
        (1430, 0, 110, 60, 28, 4),
        (1320, 60, 110, 100, 4, 28),
        (1430, 60, 110, 100, 4, 28),
    ]
    for idx, (ox, oy, ow, oh, tc, bc) in enumerate(orig_rects, start=1):
        til_lines.extend([
            f"[IMG{idx}]",
            f"SOURCE_RECT={ox},{oy},{ow},{oh}",
            f"INNER_RECT={ox + 28},{oy + tc},{ow - 56},{oh - tc - bc}",
            "SCALE=1,1,1,1,1",
            "",
        ])

    for t_id, px, py, pw, ph, im in placed_tiles:
        atlas.paste(im, (px, py))
        til_lines.extend([
            f"[IMG{t_id}]",
            f"SOURCE_RECT={px},{py},{pw},{ph}",
            "",
        ])

    buf = io.BytesIO()
    atlas.save(buf, format="PNG", optimize=True)
    anjian_bytes = buf.getvalue()
    til_text = "\r\n".join(til_lines) + "\r\n"

    land_py26 = build_pad_layout_ini(land_defs, style_map, is_land=True, is_en=False)
    land_en26 = build_pad_layout_ini(land_defs, style_map, is_land=True, is_en=True)
    port_py26 = build_pad_layout_ini(port_defs, style_map, is_land=False, is_en=False)
    port_en26 = build_pad_layout_ini(port_defs, style_map, is_land=False, is_en=True)

    return anjian_bytes, til_text, "\r\n\r\n".join(extra_css), land_py26, land_en26, port_py26, port_en26


def build_all_pad_bds():
    with zipfile.ZipFile(ORIG_BDS, "r") as zin:
        anjian_bytes, til_text, extra_css, land_py26, land_en26, port_py26, port_en26 = build_pad_theme_assets(zin, is_dark=False)

        # 1. Flat-root .bds matching Mi Pad's active skin slot (a5b04952c408c7a917e520b509d8e690.bds)
        live_out = BUILD_DIR / "pad_live.bds"
        with zipfile.ZipFile(live_out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
            for item in zin.infolist():
                fn = item.filename
                data = zin.read(fn)
                if fn == "res/anjian.png":
                    data = anjian_bytes
                elif fn == "res/anjian.til":
                    data = til_text.encode("utf-8")
                elif fn == "res/default.css":
                    css_txt = data.decode("utf-8-sig", errors="replace").rstrip() + "\r\n\r\n" + extra_css + "\r\n"
                    data = css_txt.encode("utf-8")
                elif fn == "land/gen.ini":
                    txt = data.decode("utf-8-sig", errors="replace").replace("SIZE=1920,709", "SIZE=1920,680").replace("SIZE=1920,720", "SIZE=1920,680")
                    data = txt.encode("utf-8")
                elif fn == "port/gen.ini":
                    txt = data.decode("utf-8-sig", errors="replace").replace("SIZE=1125,536", "SIZE=1125,536")
                    data = txt.encode("utf-8")
                elif fn in ("land/py_26.ini", "land/def_26.ini"):
                    data = land_py26.encode("utf-8")
                elif fn in ("land/en_26.ini", "land/en_26s.ini"):
                    data = land_en26.encode("utf-8")
                elif fn in ("port/py_26.ini", "port/def_26.ini"):
                    data = port_py26.encode("utf-8")
                elif fn in ("port/en_26.ini", "port/en_26s.ini"):
                    data = port_en26.encode("utf-8")
                elif fn in ("demo.png", "preview.png"):
                    prev_p = BUILD_DIR / "previews" / "preview_pad_portrait_v2.png"
                    if prev_p.exists():
                        im_prev = Image.open(prev_p).convert("RGB").resize((540, 360), Image.Resampling.LANCZOS)
                        b_prev = io.BytesIO()
                        im_prev.save(b_prev, format="PNG", optimize=True)
                        data = b_prev.getvalue()
                elif fn == "Info.txt":
                    data = "\ufeffName=Write 森林集 Pad\r\nStyle=default\r\nSupportPlatform=A\r\nSkinFlags=1\r\nSkinType=1\r\nAbilities=Sound\r\nVersionCode=8".encode("utf-8")
                elif fn == "Token.txt":
                    data = "\ufeffe9f48396a84ca1e35b2964f943da20d5".encode("utf-8")
                zout.writestr(item.filename, data)

        print(f"[OK] Built {live_out} ({live_out.stat().st_size:,} bytes)")

        # Copy to final deliverable directory as well
        final_bds = OUT_DIR / "Write-森林集-Pad.bds"
        final_bds.write_bytes(live_out.read_bytes())
        print(f"[OK] Saved deliverable {final_bds}")


if __name__ == "__main__":
    build_all_pad_bds()
