import argparse
import io
import os
import re
import sys
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")

OUT_DIR = Path(__file__).resolve().parents[1]
BUILD_DIR = OUT_DIR / "_build"
EXTRACTED_DIR = BUILD_DIR / "extracted"

TEXT_EXTS = {".ini", ".cnd", ".css", ".til", ".txt", ".pop"}

# Base keycap styles in default.css -> (normal_tile_1based, pressed_tile_1based, src_x, src_y, src_w, src_h, top_cap, bottom_cap)
BASE_ANJIAN_STYLES = {
    230: (1, 2, 0, 0, 110, 120, 28, 28),
    231: (3, 4, 220, 0, 110, 120, 28, 28),
    232: (5, 6, 440, 0, 110, 120, 28, 28),
    233: (7, 8, 660, 0, 110, 120, 28, 28),
    234: (9, 10, 880, 0, 110, 120, 28, 28),
    235: (11, 12, 1100, 0, 110, 120, 28, 28),
    237: (13, 14, 1320, 0, 110, 60, 28, 4),
    238: (15, 16, 1320, 60, 110, 100, 4, 28),
}

# Dedicated English legends; Chinese layouts continue to use STYLE170..176.
ENGLISH_SYMBOL_STYLES = {
    178: '"', 179: "\\", 180: "+", 181: "=", 182: "`",
    183: "<", 184: ">", 185: "^", 186: "[", 187: "]",
    188: "{", 189: "}", 190: "|", 191: "...",
    192: "€", 193: "•", 194: "°", 195: "§",
}

# Separate legends keep full-width Chinese punctuation out of English layouts.
CHINESE_26_SYMBOL_STYLES = {
    202: "￥", 203: "——", 204: "【", 205: "】", 206: "～",
    207: "×", 208: "÷", 209: "「", 210: "」", 211: "‘", 212: "’",
    213: "·", 214: "……", 215: "！", 216: "（", 217: "）",
    218: "：", 219: "；", 220: "？",
}


def nine_slice_resize(tile: Image.Image, target_w: int, target_h: int, left: int = 28, right: int = 28, top: int = 28, bottom: int = 28) -> Image.Image:
    """
    9-slice resize a keycap tile to (target_w, target_h) preserving circular corners
    and stretching only the flat center bands.
    """
    sw, sh = tile.size
    if (sw, sh) == (target_w, target_h):
        return tile.copy()

    if target_h < top + bottom or target_w < left + right:
        scale_factor = min(target_w / sw, target_h / sh, 1.0)
        scaled_sw = max(4, int(round(sw * scale_factor)))
        scaled_sh = max(4, int(round(sh * scale_factor)))
        tile = tile.resize((scaled_sw, scaled_sh), Image.Resampling.LANCZOS)
        sw, sh = scaled_sw, scaled_sh
        left = max(2, int(round(left * scale_factor)))
        right = max(2, int(round(right * scale_factor)))
        top = max(2, int(round(top * scale_factor)))
        bottom = max(2, int(round(bottom * scale_factor)))

    l_cap = min(left, target_w // 2)
    r_cap = min(right, target_w - l_cap)
    t_cap = min(top, target_h // 2)
    b_cap = min(bottom, target_h - t_cap)

    out = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))

    tl = tile.crop((0, 0, left, top))
    tr = tile.crop((sw - right, 0, sw, top))
    bl = tile.crop((0, sh - bottom, left, sh))
    br = tile.crop((sw - right, sh - bottom, sw, sh))

    if (l_cap, t_cap) != (left, top):
        tl = tl.resize((l_cap, t_cap), Image.Resampling.LANCZOS)
    if (r_cap, t_cap) != (right, top):
        tr = tr.resize((r_cap, t_cap), Image.Resampling.LANCZOS)
    if (l_cap, b_cap) != (left, bottom):
        bl = bl.resize((l_cap, b_cap), Image.Resampling.LANCZOS)
    if (r_cap, b_cap) != (right, bottom):
        br = br.resize((r_cap, b_cap), Image.Resampling.LANCZOS)

    out.paste(tl, (0, 0))
    out.paste(tr, (target_w - r_cap, 0))
    out.paste(bl, (0, target_h - b_cap))
    out.paste(br, (target_w - r_cap, target_h - b_cap))

    mid_w = target_w - l_cap - r_cap
    mid_h = target_h - t_cap - b_cap

    if mid_w > 0:
        t_edge = tile.crop((left, 0, sw - right, top)).resize((mid_w, t_cap), Image.Resampling.BILINEAR)
        b_edge = tile.crop((left, sh - bottom, sw - right, sh)).resize((mid_w, b_cap), Image.Resampling.BILINEAR)
        out.paste(t_edge, (l_cap, 0))
        out.paste(b_edge, (l_cap, target_h - b_cap))

    if mid_h > 0:
        l_edge = tile.crop((0, top, left, sh - bottom)).resize((l_cap, mid_h), Image.Resampling.BILINEAR)
        r_edge = tile.crop((sw - right, top, sw, sh - bottom)).resize((r_cap, mid_h), Image.Resampling.BILINEAR)
        out.paste(l_edge, (0, t_cap))
        out.paste(r_edge, (target_w - r_cap, t_cap))

    if mid_w > 0 and mid_h > 0:
        center = tile.crop((left, top, sw - right, sh - bottom)).resize((mid_w, mid_h), Image.Resampling.BILINEAR)
        out.paste(center, (l_cap, t_cap))

    return out


def decode_text_clean(raw: bytes) -> str:
    """Decode UTF-8 (with or without BOM) or GBK/GB18030, then scrub any corrupted U+FFFD lines."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gb18030", errors="replace")
    lines = []
    for line in text.splitlines():
        if "\ufffd" in line or "锟" in line or "妫灄" in line:
            if line.strip().startswith("INFO="):
                line = "INFO=微信公众号----森林集"
            else:
                line = line.replace("\ufffd", "")
        lines.append(line)
    return "\r\n".join(lines) + "\r\n"


def parse_ini_sections(text: str):
    """
    Parse an INI/CND/TIL/CSS text into an ordered list of [section_name_or_None, [[key_or_None, val_or_line], ...]].
    """
    sections = []
    cur_name = None
    cur_items = []
    for raw_line in text.splitlines():
        s = raw_line.strip()
        if s.startswith("[") and s.endswith("]"):
            if cur_name is not None or cur_items:
                sections.append([cur_name, cur_items])
            cur_name = s[1:-1].strip()
            cur_items = []
        elif "=" in raw_line and not s.startswith(";") and not s.startswith("#"):
            k, v = raw_line.split("=", 1)
            cur_items.append([k.strip(), v.strip(" \t\r\n")])
        elif s == "":
            cur_items.append([None, ""])
        else:
            cur_items.append([None, s])
    if cur_name is not None or cur_items:
        sections.append([cur_name, cur_items])
    return sections


def serialize_ini_sections(sections) -> str:
    out_lines = []
    for sec_name, items in sections:
        if sec_name is not None:
            if out_lines and out_lines[-1] != "":
                out_lines.append("")
            out_lines.append(f"[{sec_name}]")
        for k, v in items:
            if k is None:
                if v == "" and (not out_lines or out_lines[-1] == ""):
                    continue
                out_lines.append(v)
            else:
                out_lines.append(f"{k}={v}")
    while out_lines and out_lines[-1] == "":
        out_lines.pop()
    return "\r\n".join(out_lines) + "\r\n"


def get_sec_val(items, key: str):
    key_u = key.upper()
    for k, v in items:
        if k is not None and k.upper() == key_u:
            return v
    return None


def set_sec_val(items, key: str, val: str):
    key_u = key.upper()
    for pair in items:
        if pair[0] is not None and pair[0].upper() == key_u:
            pair[1] = str(val)
            return
    insert_idx = len(items)
    while insert_idx > 0 and items[insert_idx - 1][0] is None and items[insert_idx - 1][1] == "":
        insert_idx -= 1
    items.insert(insert_idx, [key, str(val)])


def remove_sec_key(items, key: str):
    key_u = key.upper()
    items[:] = [pair for pair in items if not (pair[0] is not None and pair[0].upper() == key_u)]


def dedup_sec_keys(items):
    """Keep the first occurrence of duplicate keys (except INFO)."""
    seen = set()
    cleaned = []
    for k, v in items:
        if k is not None and k.upper() != "INFO":
            ku = k.upper()
            if ku in seen:
                continue
            seen.add(ku)
        cleaned.append([k, v])
    return cleaned


def make_android_info_txt(name: str, dual_mode: bool = True, has_sound: bool = True) -> str:
    lines = [
        f"Name={name}",
        "Author=ForestPro",
        "Style=default",
        "SupportPlatform=A",
        "SkinFlags=1",
        "SkinType=1",
    ]
    if dual_mode:
        lines.append("SupportDarkMode=1")
    if has_sound:
        lines.append("Abilities=Sound")
    # Bump on release. Deploy via ImeUpdateActivity + Apply under a versioned
    # filename; overwriting an already active .bds does not reload its keymap.
    lines.append("VersionCode=13")
    return "\r\n".join(lines) + "\r\n"


def _draw_clip_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, color, s: int = 4):
    lw = max(2, 2 * s + 1)
    draw.rounded_rectangle([cx - 7 * s, cy - 6 * s, cx + 7 * s, cy + 9 * s], radius=2 * s, outline=color, width=lw)
    draw.rounded_rectangle([cx - 4 * s, cy - 9 * s, cx + 4 * s, cy - 4 * s], radius=1 * s, fill=color)
    draw.line([(cx - 4 * s, cy), (cx + 4 * s, cy)], fill=color, width=max(1, lw - 2))
    draw.line([(cx - 4 * s, cy + 4 * s), (cx + 4 * s, cy + 4 * s)], fill=color, width=max(1, lw - 2))


def build_sym_assets(theme_dir: Path):
    """
    1. Load res/sym.png and res/sym2.png.
    2. Redraw (140,160,35,35 -> STYLE624 on ENTER) as a crisp white clipboard icon
       so it is 100% visible on the coral-red ENTER key in both Light and Dark modes.
    3. Emit contiguous sym.til / sym2.til (IMG1..19, TILE_NUM=19).
    """
    out_nm = Image.open(theme_dir / "res" / "sym.png").convert("RGBA")
    out_hl = Image.open(theme_dir / "res" / "sym2.png").convert("RGBA")

    s = 4
    clip_canvas = Image.new("RGBA", (35 * s, 35 * s), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(clip_canvas)
    _draw_clip_icon(cdraw, (35 * s) // 2, (35 * s) // 2, (255, 255, 255, 255), s=s)
    clip_icon = clip_canvas.resize((35, 35), Image.Resampling.LANCZOS)

    clear_35 = Image.new("RGBA", (35, 35), (0, 0, 0, 0))
    out_nm.paste(clear_35, (140, 160))
    out_nm.paste(clip_icon, (140, 160), clip_icon)
    out_hl.paste(clear_35, (140, 160))
    out_hl.paste(clip_icon, (140, 160), clip_icon)

    til_lines = [
        "[GLOBAL]",
        "USE_ALPHA=1",
        "TILE_NUM=19",
        "",
    ]
    for idx in range(1, 11):
        til_lines.extend([
            f"[IMG{idx}]",
            f"SOURCE_RECT={(idx - 1) * 110},0,110,80",
            "",
        ])
    til_lines.extend([
        "[IMG11]",
        "SOURCE_RECT=0,80,68,68",
        "INNER_RECT=34,114,1,1",
        "",
    ])
    for idx, sx in enumerate([0, 35, 70, 105, 140, 175, 210, 245], start=12):
        til_lines.extend([
            f"[IMG{idx}]",
            f"SOURCE_RECT={sx},160,35,35",
            "",
        ])

    buf_nm = io.BytesIO()
    out_nm.save(buf_nm, format="PNG", optimize=True)
    buf_hl = io.BytesIO()
    out_hl.save(buf_hl, format="PNG", optimize=True)
    til_text = "\r\n".join(til_lines) + "\r\n"
    return buf_nm.getvalue(), buf_hl.getvalue(), til_text


def patch_gen_ini_android(orientation: str, text: str) -> str:
    """
    1. Set [HINT] LAYOUT_NAME=hint2 (and remove TYPE=0) so swipe preview popups work globally.
    2. Add [OFFSET144] and [OFFSET145] for the left ('L') and right ('R') cursor labels
       on the 14-key GH key, and [OFFSET8] for the 'sel' (F61) label on SH.
    3. Set [OFFSET23..25] on KEY31 (comma: top=UP, bottom=DOWN, left=LEFT 'home')
       and [OFFSET32,33,35] on KEY33 (period: top=UP, bottom=DOWN, right=RIGHT 'end').
    4. Set [OFFSET64,65,73,80,81,82] for number-pad (num_9/num_26) swipe sub-labels.
    """
    sections = parse_ini_sections(text)
    lr_x = 64 if orientation == "port" else 105
    top_y = -44 if orientation == "port" else -59
    bot_y = 38 if orientation == "port" else 51
    comma_left_x = -27 if orientation == "port" else -48
    period_right_x = 28 if orientation == "port" else 50
    num_side_x = 88 if orientation == "port" else 150

    target_offsets = {
        "OFFSET8": f"0,{bot_y}",
        "OFFSET23": f"0,{top_y}",
        "OFFSET24": f"0,{bot_y}",
        "OFFSET25": f"{comma_left_x},0",
        "OFFSET32": f"0,{top_y}",
        "OFFSET33": f"0,{bot_y}",
        "OFFSET35": f"{period_right_x},0",
        "OFFSET62": f"{num_side_x},{top_y}",
        "OFFSET64": f"0,{top_y}",
        "OFFSET65": f"-{num_side_x},{top_y}",
        "OFFSET73": f"0,{top_y}",
        "OFFSET80": f"0,{bot_y}",
        "OFFSET81": f"-{num_side_x},0",
        "OFFSET82": f"{num_side_x},0",
        "OFFSET144": f"-{lr_x},0",
        "OFFSET145": f"{lr_x},0",
    }
    found = set()

    for sec_name, items in sections:
        if sec_name == "HINT":
            set_sec_val(items, "LAYOUT_NAME", "hint2")
            remove_sec_key(items, "TYPE")
        elif sec_name in target_offsets:
            found.add(sec_name)
            set_sec_val(items, "POS", target_offsets[sec_name])

    for off_name, pos_val in target_offsets.items():
        if off_name not in found:
            sections.append([off_name, [["POS", pos_val]]])
    return serialize_ini_sections(sections)


def add_hold_symbol_popups(sections):
    """Use Baidu's native HOLDSYM bar, not HOLD (which commits immediately).

    Character keys expose both cases and all literal directional alternatives.
    Pure function keys keep their native repeat/voice/layout-switch behavior.
    The native bar is character-based; repeated ellipses/dashes are deduplicated
    here while their multi-character directional actions remain unchanged.
    """
    for name, items in sections:
        if not name or not re.match(r"^(KEY(_TOP_)?|TIP)\d+$", name, re.I):
            continue
        values = [get_sec_val(items, k) or "" for k in
                  ("LEFT", "CENTER", "RIGHT", "SHOW", "UP", "DOWN", "HOLD", "HOLDSYM")]
        literal = [v for v in values if v and not re.match(r"^[A-Z][\d+]|^[A-Z]\+", v)]
        if not literal:
            continue
        letters = []
        for v in literal:
            for c in v:
                if c.isascii() and c.isalpha() and c.lower() not in letters:
                    letters.append(c.lower())
        chars = []
        for c in letters:
            chars.extend((c, c.upper()))
        for v in literal:
            for c in v:
                if not c.isspace() and c not in chars:
                    chars.append(c)
        if chars:
            set_sec_val(items, "HOLDSYM", "".join(chars))
            remove_sec_key(items, "HOLD")


def patch_english_ascii_punctuation(sections):
    """Expose all 32 US ASCII punctuation characters without losing edit gestures.

    Patch the letter's state TIPs as well as its primary KEY, so temporary Shift
    and Caps Lock cannot bring back Chinese symbols or obsolete popup choices.
    Preserve the letter-key upper swipes, digits, cursor and native slide-delete.
    Use four distinct Western extensions (€ • ° §) in formerly duplicate slots.
    """
    down_symbols = {
        "w": ("€", 192),
        "y": ("^", 185), "u": ("[", 186), "i": ("]", 187), "o": ("~", 119),
        "h": ("$", 113), "j": ("|", 190), "k": ("{", 188), "l": ("}", 189),
        "z": ('"', 178), "x": ("\\", 179), "c": ("+", 180), "v": ("=", 181),
        "b": ("`", 182), "n": ("<", 183), "m": (">", 184),
    }
    for name, items in sections:
        if not name or not re.match(r"^(KEY|TIP)\d+$", name, re.I):
            continue
        center = get_sec_val(items, "CENTER") or ""
        if len(center) == 1 and center.isascii() and center.isalpha():
            # add_hold_symbol_popups must only see the new directional choices.
            remove_sec_key(items, "HOLD")
            remove_sec_key(items, "HOLDSYM")
            if center.lower() in down_symbols:
                symbol, style_id = down_symbols[center.lower()]
                fore = (get_sec_val(items, "FORE_STYLE") or "").split(",")
                assert len(fore) >= 2, f"{name}: missing center/upper legend"
                set_sec_val(items, "DOWN", symbol)
                set_sec_val(items, "FORE_STYLE", ",".join(fore[:2] + [str(style_id)]))
                set_sec_val(items, "POS_TYPE", "1,2,3")
        elif name == "KEY31" or name == "TIP3":
            # Retain TIP3's native F25 action/icon, but never its Chinese flicks.
            base_fore = "666" if name == "KEY31" else "912"
            set_sec_val(items, "UP", "•")
            set_sec_val(items, "DOWN", "...")
            set_sec_val(items, "FORE_STYLE", f"{base_fore},193,191,135")
            remove_sec_key(items, "HOLD")
            remove_sec_key(items, "HOLDSYM")
        elif name == "KEY33":
            set_sec_val(items, "UP", "°")
            set_sec_val(items, "DOWN", "§")
            set_sec_val(items, "FORE_STYLE", "72,194,195,136")
            remove_sec_key(items, "HOLD")
            remove_sec_key(items, "HOLDSYM")


def patch_chinese_26_punctuation(sections):
    """Mirror English's filled symbol slots, using distinct Chinese alternatives.

    Opening single/double quotes live on Z; the closing counterparts live on
    the full-stop key. Book-title, square and corner brackets remain paired.
    Numeric, editing, navigation and 14-key layouts are not changed.
    """
    upper_symbols = {
        "a": ("！", 215), "s": ("@", 111), "d": ("#", 112), "f": ("%", 114),
        "h": ("&", 115), "j": ("×", 207), "k": ("（", 216), "l": ("）", 217),
        "z": ("‘", 211), "x": ("/", 120), "c": ("-", 121), "v": ("_", 122),
        "b": ("：", 218), "n": ("；", 219), "m": ("？", 220),
    }
    down_symbols = {
        "w": ("￥", 202), "y": ("——", 203), "u": ("【", 204), "i": ("】", 205), "o": ("～", 206),
        "h": ("$", 113), "j": ("÷", 208), "k": ("「", 209), "l": ("」", 210),
        "z": ("“", 172), "x": ("\\", 179), "c": ("+", 180), "v": ("=", 181),
        "b": ("·", 213), "n": ("《", 175), "m": ("》", 176),
    }
    for name, items in sections:
        if not name or not re.match(r"^(KEY|TIP)\d+$", name, re.I):
            continue
        center = get_sec_val(items, "CENTER") or ""
        if len(center) == 1 and center.isascii() and center.isalpha():
            remove_sec_key(items, "HOLD")
            remove_sec_key(items, "HOLDSYM")
            letter = center.lower()
            fore = (get_sec_val(items, "FORE_STYLE") or "").split(",")
            if letter in upper_symbols:
                symbol, style_id = upper_symbols[letter]
                set_sec_val(items, "UP", symbol)
                fore[1] = str(style_id)
            if letter in down_symbols:
                symbol, style_id = down_symbols[letter]
                set_sec_val(items, "DOWN", symbol)
                fore = fore[:2] + [str(style_id)]
                set_sec_val(items, "POS_TYPE", "1,2,3")
            if letter in upper_symbols or letter in down_symbols:
                set_sec_val(items, "FORE_STYLE", ",".join(fore))
        elif name == "KEY31":
            set_sec_val(items, "UP", "、")
            set_sec_val(items, "DOWN", "……")
            set_sec_val(items, "FORE_STYLE", "913,170,214,135")
            remove_sec_key(items, "HOLD")
            remove_sec_key(items, "HOLDSYM")
        elif name == "KEY33":
            set_sec_val(items, "UP", "’")
            set_sec_val(items, "DOWN", "”")
            set_sec_val(items, "FORE_STYLE", "914,212,173,136")
            remove_sec_key(items, "HOLD")
            remove_sec_key(items, "HOLDSYM")


def patch_layout_ini_android(orientation: str, fname: str, text: str):
    """
    Apply the full redesigned keyboard layout and gestures to Android INI files:
    - 14-Key (py_9.ini, py_14.ini, def_9.ini):
      * Row 1 (QW, ER, TY, UI, OP):
        UP = 1, 2, 3, 4, 5
        DOWN = menu(F31, 620), @(177), forward-del(F37, 145), clear(F48, 146), tab/indent(　　, 134)
      * Row 2 (AS, DF, GH, JK, L):
        AS: UP=6, DOWN=select(F47, 130)
        DF: UP=7, DOWN=cut(F44, 131)
        GH: UP=F49, DOWN=F50; LEFT=g and RIGHT=h must remain registered
            for ambiguous 14-key input. Do not replace them with F51/F52.
        JK: UP=9, DOWN=copy(F45, 132)
        L : UP=0, DOWN=paste(F46, 133 -> direct clipboard paste on Android!)
      * Row 3 (SH, ZX, CV, BN, M, BACK):
        ZX: UP=：(123), DOWN=“(172)
        CV: UP=；(124), DOWN=”(173)
        BN: UP=8(107), DOWN=（(117)
        M : UP=？(125), DOWN=）(118)
        BACK: CENTER=F36 (backspace), DOWN=F36 (delete), LEFT=F36 (delete), UP=F48 (clear), HOLD=F36
      * Row 4 (123, EN, ，, SPACE, 。, ENTER):
        123: CENTER=F6, UP=F7 (emoji)
        EN : CENTER=F16, UP=F84 (input mode / 14-26 switch)
        ， : CENTER=，, LEFT=F42 (home, 135), UP=、(170), DOWN=……(171)
        SPACE: CENTER=F38, UP=F77 (phrase), LEFT=F51, RIGHT=F52, HOLD=F72 (voice)
        。 : CENTER=。, RIGHT=F43 (end, 136), UP=！(110), DOWN=——(174)
        ENTER: CENTER=F39, UP=F76 (clipboard panel, 624)
    - 26-Key (py_26.ini, def_26.ini, en_26.ini, en_26s.ini):
      * Row 1: Q=menu(F31, 620), W=@(177), E=forward-del(F37, 145), R=clear(F48, 146), T=tab/indent(　　, 134), P=hide(F8, 621)
      * Row 2: A=select(F47, 130), S=cut(F44, 131), D=copy(F45, 132), F=paste(F46, 133), G=4-way cursor(U/D/L/R F49..F52)
      * Row 3:
        Z: UP=~, DOWN=、(160)
        X: UP=/, DOWN=……(161)
        C: UP=-, DOWN=——(164)
        V: UP=_, DOWN=“(162)
        B: UP=：, DOWN=”(163)
        N: UP=；, DOWN=《(165)
        M: UP=？, DOWN=》(166)
        BACK: CENTER=F36, DOWN=F36, LEFT=F36, UP=F48, HOLD=F36
      * Row 4:
        123: UP=F7, EN/CH: UP=F84
        ， : CENTER=，, LEFT=F42(home, 135), UP=、(170), DOWN=……(171)
        SPACE: UP=F77
        。 : CENTER=。, RIGHT=F43(end, 136), UP=！(110), DOWN=——(174)
        ENTER: UP=F76(clip, 624)
    - Number pad (num_9.ini, num_26.ini):
      * 1=menu(F31), 2=select(F47), 3=hide(F8), 4=copy(F45), 5=cut(F44), 6=paste(F46),
        7=home(F42, 135), 8=tab(　　, 134), 9=end(F43, 136), 0=undo(F40, 144), .=del(F37, 145),
        BACK=delete(F36), ENTER=clip(F76)
    - English 26-key overlays use ASCII punctuation plus € • ° § (including Shift/Caps
      state TIPs); see patch_english_ascii_punctuation for the complete mapping.
    """
    fl = fname.lower()
    sections = parse_ini_sections(text)

    for sec in sections:
        sec[1] = dedup_sec_keys(sec[1])

    sec_map = {sec_name: items for sec_name, items in sections if sec_name is not None}

    # Global [HINT] fix: enable swipe character preview bubble (hint2) on all keyboard layouts
    if "HINT" in sec_map:
        set_sec_val(sec_map["HINT"], "LAYOUT_NAME", "hint2")
        remove_sec_key(sec_map["HINT"], "TYPE")

    # Global BACK (CENTER=F36) fix: remove LEFT/RIGHT/DOWN overrides so Baidu Input's native
    # SlideDelete (left-slide continuous delete + right-slide restore/undo + up-slide clear / down-slide revoke) works unimpeded!
    for sec_name, items in sec_map.items():
        if sec_name and sec_name.startswith("KEY") and get_sec_val(items, "CENTER") == "F36":
            remove_sec_key(items, "LEFT")
            remove_sec_key(items, "RIGHT")
            remove_sec_key(items, "DOWN")
            set_sec_val(items, "UP", "F48")
            set_sec_val(items, "HOLD", "F36")

    # 1. 14-Key Layouts (py_9.ini, py_14.ini, def_9.ini)
    if fl in {"py_9.ini", "py_14.ini", "def_9.ini"}:
        letter_14_fixes = {
            "KEY1": ("q", "w", "q", "w", "1", "F31", "1,2,100,620", "140,141,142,143"),
            "KEY2": ("e", "r", "r", "e", "2", "@", "3,4,101,177", "140,141,142,143"),
            "KEY3": ("t", "y", "t", "y", "3", "F37", "5,6,102,145", "140,141,142,143"),
            "KEY4": ("u", "i", "i", "u", "4", "F48", "7,8,103,146", "140,141,142,143"),
            "KEY5": ("o", "p", "p", "o", "5", "　　", "9,10,104,134", "140,141,142,143"),
            "KEY11": ("a", "s", "s", "a", "6", "F47", "11,12,105,130", "140,141,142,143"),
            "KEY12": ("d", "f", "d", "f", "7", "F44", "13,14,106,131", "140,141,142,143"),
            "KEY13": ("g", "h", "g", "h", "F49", "F50", "15,16,140,143,141,142", "140,141,142,143,144,145"),
            "KEY14": ("j", "k", "j", "k", "9", "F45", "17,18,108,132", "140,141,142,143"),
            "KEY15": ("l", "l", "l", "l", "0", "F46", "19,109,133", "1,2,3"),
            "KEY20": ("z", "x", "x", "z", "：", "“", "20,21,123,172", "140,141,142,143"),
            "KEY21": ("c", "v", "v", "c", "；", "”", "22,23,124,173", "140,141,142,143"),
            "KEY22": ("b", "n", "b", "n", "8", "（", "24,25,107,117", "140,141,142,143"),
            "KEY23": ("m", "m", "m", "m", "？", "）", "26,125,118", "1,2,3"),
        }
        for k_sec, (l_ch, r_ch, c_ch, s_ch, up_act, dn_act, f_style, p_type) in letter_14_fixes.items():
            if k_sec in sec_map:
                items = sec_map[k_sec]
                set_sec_val(items, "LEFT", l_ch)
                set_sec_val(items, "RIGHT", r_ch)
                set_sec_val(items, "CENTER", c_ch)
                set_sec_val(items, "SHOW", s_ch)
                set_sec_val(items, "UP", up_act)
                if dn_act is None:
                    remove_sec_key(items, "DOWN")
                    remove_sec_key(items, "HOLD")
                else:
                    set_sec_val(items, "DOWN", dn_act)
                    if k_sec != "KEY13":
                        set_sec_val(items, "HOLD", dn_act)
                    else:
                        remove_sec_key(items, "HOLD")
                set_sec_val(items, "FORE_STYLE", f_style)
                set_sec_val(items, "POS_TYPE", p_type)

        # SH (KEY27, TIP1, TIP2): DOWN=F61 (toggle Selection Mode 'sel', 630), LEFT=F51, RIGHT=F52
        for sh_sec in ("KEY27", "TIP1", "TIP2"):
            if sh_sec in sec_map:
                items = sec_map[sh_sec]
                fs = get_sec_val(items, "FORE_STYLE") or ""
                fs_parts = [x.strip() for x in fs.split(",") if x.strip()][:2]
                fs_parts.append("630")
                set_sec_val(items, "FORE_STYLE", ",".join(fs_parts))
                set_sec_val(items, "POS_TYPE", "6,7,8,9,10")
                set_sec_val(items, "DOWN", "F61")
                set_sec_val(items, "HOLD", "F61")
                set_sec_val(items, "LEFT", "F51")
                set_sec_val(items, "RIGHT", "F52")

        # BACK (KEY28): UP=F48, CENTER=F36 (no LEFT/RIGHT/DOWN override -> native SlideDelete!)
        if "KEY28" in sec_map:
            set_sec_val(sec_map["KEY28"], "UP", "F48")
            remove_sec_key(sec_map["KEY28"], "DOWN")
            remove_sec_key(sec_map["KEY28"], "LEFT")
            remove_sec_key(sec_map["KEY28"], "RIGHT")
            set_sec_val(sec_map["KEY28"], "CENTER", "F36")
            set_sec_val(sec_map["KEY28"], "HOLD", "F36")

        # 123 (KEY29): UP=F7, CENTER=F6
        if "KEY29" in sec_map:
            set_sec_val(sec_map["KEY29"], "FORE_STYLE", "908,625")
            set_sec_val(sec_map["KEY29"], "POS_TYPE", "6,15,16,17,18")
            set_sec_val(sec_map["KEY29"], "CENTER", "F6")
            set_sec_val(sec_map["KEY29"], "UP", "F7")
            remove_sec_key(sec_map["KEY29"], "DOWN")

        # EN (KEY30): UP=F84 (Android input method / 14-26 layout switcher), CENTER=F16
        if "KEY30" in sec_map:
            set_sec_val(sec_map["KEY30"], "FORE_STYLE", "909,626")
            set_sec_val(sec_map["KEY30"], "POS_TYPE", "6,19,20,21,22")
            set_sec_val(sec_map["KEY30"], "CENTER", "F16")
            set_sec_val(sec_map["KEY30"], "UP", "F84")
            remove_sec_key(sec_map["KEY30"], "DOWN")

        # Comma (KEY31): CENTER=，, LEFT=F42 (home, 135), UP=、(170), DOWN=……(171)
        if "KEY31" in sec_map:
            set_sec_val(sec_map["KEY31"], "FORE_STYLE", "913,170,171,135")
            set_sec_val(sec_map["KEY31"], "POS_TYPE", "6,23,24,25")
            set_sec_val(sec_map["KEY31"], "CENTER", "，")
            set_sec_val(sec_map["KEY31"], "UP", "、")
            set_sec_val(sec_map["KEY31"], "DOWN", "……")
            set_sec_val(sec_map["KEY31"], "LEFT", "F42")
            remove_sec_key(sec_map["KEY31"], "RIGHT")
            set_sec_val(sec_map["KEY31"], "HOLD", "F42")

        # Period: tap=。, up=！, down=——; keep right=end.
        if "KEY33" in sec_map:
            set_sec_val(sec_map["KEY33"], "FORE_STYLE", "914,110,174,136")
            set_sec_val(sec_map["KEY33"], "POS_TYPE", "6,32,33,35")
            set_sec_val(sec_map["KEY33"], "CENTER", "。")
            set_sec_val(sec_map["KEY33"], "UP", "！")
            set_sec_val(sec_map["KEY33"], "DOWN", "——")
            set_sec_val(sec_map["KEY33"], "RIGHT", "F43")
            remove_sec_key(sec_map["KEY33"], "LEFT")
            set_sec_val(sec_map["KEY33"], "HOLD", "F43")

        # SPACE (KEY32): UP=F77
        if "KEY32" in sec_map:
            set_sec_val(sec_map["KEY32"], "FORE_STYLE", "919,623")
            set_sec_val(sec_map["KEY32"], "POS_TYPE", "27,28,29,30,31")
            remove_sec_key(sec_map["KEY32"], "DOWN")

        # ENTER (KEY34): UP=F76 (clipboard panel)
        if "KEY34" in sec_map:
            set_sec_val(sec_map["KEY34"], "FORE_STYLE", "917,624")
            set_sec_val(sec_map["KEY34"], "POS_TYPE", "36,37,38,39,40")
            set_sec_val(sec_map["KEY34"], "UP", "F76")
            remove_sec_key(sec_map["KEY34"], "DOWN")
            set_sec_val(sec_map["KEY34"], "HOLD", "F76")

    # 2. 26-Key Layouts (py_26.ini, def_26.ini, en_26.ini, en_26s.ini)
    if fl in {"py_26.ini", "def_26.ini", "en_26.ini", "en_26s.ini"}:
        # Q (KEY1 / TIP11): DOWN=F31 (menu, 620)
        for k_sec in ("KEY1", "TIP11"):
            if k_sec in sec_map:
                set_sec_val(sec_map[k_sec], "DOWN", "F31")
                set_sec_val(sec_map[k_sec], "HOLD", "F31")
        # P (KEY10 / TIP20): DOWN=F8 (hide keyboard, 621 — native on Android!)
        for k_sec in ("KEY10", "TIP20"):
            if k_sec in sec_map:
                items = sec_map[k_sec]
                fs = get_sec_val(items, "FORE_STYLE") or ""
                fs_parts = [x.strip() for x in fs.split(",") if x.strip()][:2]
                fs_parts.append("621")
                set_sec_val(items, "FORE_STYLE", ",".join(fs_parts))
                set_sec_val(items, "POS_TYPE", "1,2,3")
                set_sec_val(items, "DOWN", "F8")
                set_sec_val(items, "HOLD", "F8")

        for key_sec, down_code, style_id in [
            # Row 1 (W, E, R, T): @(177), forward-del(F37, 145), clear(F48, 146), tab/indent(　　, 134)
            ("KEY2", "@", "177"),
            ("KEY3", "F37", "145"),
            ("KEY4", "F48", "146"),
            ("KEY5", "　　", "134"),
            ("TIP12", "@", "177"),
            ("TIP13", "F37", "145"),
            ("TIP14", "F48", "146"),
            ("TIP15", "　　", "134"),
            # Row 2 (A, S, D, F): select(F47, 130), cut(F44, 131), copy(F45, 132), paste(F46, 133 -> direct paste on Android!)
            ("KEY11", "F47", "130"),
            ("KEY12", "F44", "131"),
            ("KEY13", "F45", "132"),
            ("KEY14", "F46", "133"),
            ("TIP21", "F47", "130"),
            ("TIP22", "F44", "131"),
            ("TIP23", "F45", "132"),
            ("TIP24", "F46", "133"),
        ]:
            if key_sec in sec_map:
                items = sec_map[key_sec]
                fs = get_sec_val(items, "FORE_STYLE") or ""
                fs_parts = [x.strip() for x in fs.split(",") if x.strip()][:2]
                fs_parts.append(style_id)
                set_sec_val(items, "FORE_STYLE", ",".join(fs_parts))
                set_sec_val(items, "POS_TYPE", "1,2,3")
                set_sec_val(items, "DOWN", down_code)
                set_sec_val(items, "HOLD", down_code)

        # Row 3 (Z..M -> KEY20..26 & TIP30..36): add high-frequency Chinese punctuation on DOWN=
        for k_sec, t_sec, dn_punc, st_id in [
            ("KEY20", "TIP30", "、", "170"),
            ("KEY21", "TIP31", "……", "171"),
            ("KEY22", "TIP32", "——", "174"),
            ("KEY23", "TIP33", "“", "172"),
            ("KEY24", "TIP34", "”", "173"),
            ("KEY25", "TIP35", "《", "175"),
            ("KEY26", "TIP36", "》", "176"),
        ]:
            for sec_n in (k_sec, t_sec):
                if sec_n in sec_map:
                    items = sec_map[sec_n]
                    fs = get_sec_val(items, "FORE_STYLE") or ""
                    fs_parts = [x.strip() for x in fs.split(",") if x.strip()][:2]
                    fs_parts.append(st_id)
                    set_sec_val(items, "FORE_STYLE", ",".join(fs_parts))
                    set_sec_val(items, "POS_TYPE", "1,2,3")
                    set_sec_val(items, "DOWN", dn_punc)
                    set_sec_val(items, "HOLD", dn_punc)

        # SH (KEY27, TIP1, TIP2): DOWN=F61 (toggle Selection Mode 'sel', 630), LEFT=F51, RIGHT=F52
        for sh_sec in ("KEY27", "TIP1", "TIP2"):
            if sh_sec in sec_map:
                items = sec_map[sh_sec]
                fs = get_sec_val(items, "FORE_STYLE") or ""
                fs_parts = [x.strip() for x in fs.split(",") if x.strip()][:2]
                fs_parts.append("630")
                set_sec_val(items, "FORE_STYLE", ",".join(fs_parts))
                set_sec_val(items, "POS_TYPE", "6,7,8,9,10")
                set_sec_val(items, "DOWN", "F61")
                set_sec_val(items, "HOLD", "F61")
                set_sec_val(items, "LEFT", "F51")
                set_sec_val(items, "RIGHT", "F52")

        # BACK (KEY28): UP=F48, CENTER=F36 (no LEFT/RIGHT/DOWN override -> native SlideDelete!)
        if "KEY28" in sec_map:
            set_sec_val(sec_map["KEY28"], "UP", "F48")
            remove_sec_key(sec_map["KEY28"], "DOWN")
            remove_sec_key(sec_map["KEY28"], "LEFT")
            remove_sec_key(sec_map["KEY28"], "RIGHT")
            set_sec_val(sec_map["KEY28"], "CENTER", "F36")
            set_sec_val(sec_map["KEY28"], "HOLD", "F36")

        # 123 (KEY29)
        if "KEY29" in sec_map:
            set_sec_val(sec_map["KEY29"], "FORE_STYLE", "908,625")
            set_sec_val(sec_map["KEY29"], "POS_TYPE", "6,15,16,17,18")
            set_sec_val(sec_map["KEY29"], "CENTER", "F6")
            set_sec_val(sec_map["KEY29"], "UP", "F7")
            remove_sec_key(sec_map["KEY29"], "DOWN")

        # EN / CH (KEY30): UP=F84 (native Android input-method / 14-26 switcher)
        if "KEY30" in sec_map:
            base_fore = "909" if fl in {"py_26.ini", "def_26.ini"} else "910"
            set_sec_val(sec_map["KEY30"], "FORE_STYLE", f"{base_fore},626")
            set_sec_val(sec_map["KEY30"], "POS_TYPE", "6,19,20,21,22")
            set_sec_val(sec_map["KEY30"], "UP", "F84")
            remove_sec_key(sec_map["KEY30"], "DOWN")

        # Comma (KEY31) & Period (KEY33):
        #   KEY31: CENTER=，, LEFT=F42 (home, 135), UP=、(170), DOWN=……(171)
        #   KEY33: CENTER=。, RIGHT=F43 (end, 136), UP=！(110), DOWN=——(174)
        is_en26 = fl in {"en_26.ini", "en_26s.ini"}
        if "KEY31" in sec_map:
            set_sec_val(sec_map["KEY31"], "FORE_STYLE", "913,170,171,135")
            set_sec_val(sec_map["KEY31"], "POS_TYPE", "6,23,24,25")
            set_sec_val(sec_map["KEY31"], "CENTER", "," if is_en26 else "，")
            set_sec_val(sec_map["KEY31"], "UP", "、")
            set_sec_val(sec_map["KEY31"], "DOWN", "……")
            set_sec_val(sec_map["KEY31"], "LEFT", "F42")
            remove_sec_key(sec_map["KEY31"], "RIGHT")
            set_sec_val(sec_map["KEY31"], "HOLD", "F25" if is_en26 else "F42")
        if is_en26 and "TIP3" in sec_map:
            set_sec_val(sec_map["TIP3"], "FORE_STYLE", "912,170,171,135")
            set_sec_val(sec_map["TIP3"], "POS_TYPE", "6,23,24,25")
            set_sec_val(sec_map["TIP3"], "CENTER", "F25")
            set_sec_val(sec_map["TIP3"], "UP", "、")
            set_sec_val(sec_map["TIP3"], "DOWN", "……")
            set_sec_val(sec_map["TIP3"], "LEFT", "F42")
            remove_sec_key(sec_map["TIP3"], "RIGHT")
        if "KEY33" in sec_map:
            # Reuse the numeric panel's solid ASCII dot glyph for English.
            # STYLE914 is the hollow Chinese full stop, not an English period.
            period_fore = "72" if is_en26 else "914"
            set_sec_val(sec_map["KEY33"], "FORE_STYLE", f"{period_fore},110,174,136")
            set_sec_val(sec_map["KEY33"], "POS_TYPE", "6,32,33,35")
            set_sec_val(sec_map["KEY33"], "CENTER", "." if is_en26 else "。")
            set_sec_val(sec_map["KEY33"], "UP", "！")
            set_sec_val(sec_map["KEY33"], "DOWN", "——")
            set_sec_val(sec_map["KEY33"], "RIGHT", "F43")
            remove_sec_key(sec_map["KEY33"], "LEFT")
            set_sec_val(sec_map["KEY33"], "HOLD", "F43")

        # SPACE (KEY32)
        if "KEY32" in sec_map:
            set_sec_val(sec_map["KEY32"], "FORE_STYLE", "919,623")
            set_sec_val(sec_map["KEY32"], "POS_TYPE", "27,28,29,30,31")
            remove_sec_key(sec_map["KEY32"], "DOWN")

        # ENTER (KEY34): UP=F76 (open clipboard panel)
        if "KEY34" in sec_map:
            set_sec_val(sec_map["KEY34"], "FORE_STYLE", "917,624")
            set_sec_val(sec_map["KEY34"], "POS_TYPE", "36,37,38,39,40")
            set_sec_val(sec_map["KEY34"], "UP", "F76")
            remove_sec_key(sec_map["KEY34"], "DOWN")
            set_sec_val(sec_map["KEY34"], "HOLD", "F76")

    # 3. Number Pad Layouts (num_9.ini, num_26.ini)
    if fl in {"num_9.ini", "num_26.ini"}:
        if "PANEL" in sec_map:
            set_sec_val(sec_map["PANEL"], "NO_BLUR", "1")
        if "LIST" in sec_map:
            set_sec_val(sec_map["LIST"], "LIST_ORDER", "0")
            set_sec_val(sec_map["LIST"], "NAMES", "+ － × ÷ = % ￥ $ ： ； 、 @")
            set_sec_val(sec_map["LIST"], "VALUES", "+ － × ÷ = % ￥ $ ： ； 、 @")

        # 3x3 Number Grid (KEY2..KEY10):
        #   Center(61) = 1..9 (STYLE62..70)
        #   Down(63)   = menu(F31,620), select(F47,130), hide(F8,621), copy(F45,132), cut(F44,131),
        #                paste(F46,133), home(F42,135), tab(　　,134), end(F43,136)
        #   Up(64)     = ①..⑨ (STYLE641..649)
        #   Left(65)   = 壹..玖 (STYLE631..639)
        #   Right(62)  = ㊀..㊈ (STYLE651..659)
        num_grid_specs = [
            ("KEY2", "1", "62", "①", "641", "F31", "620", "壹", "631", "㊀", "651", "❶"),
            ("KEY3", "2", "63", "②", "642", "F47", "130", "贰", "632", "㊁", "652", "❷"),
            ("KEY4", "3", "64", "③", "643", "F8", "621", "叁", "633", "㊂", "653", "❸"),
            ("KEY5", "4", "65", "④", "644", "F45", "132", "肆", "634", "㊃", "654", "❹"),
            ("KEY6", "5", "66", "⑤", "645", "F44", "131", "伍", "635", "㊄", "655", "❺"),
            ("KEY7", "6", "67", "⑥", "646", "F46", "133", "陆", "636", "㊅", "656", "❻"),
            ("KEY8", "7", "68", "⑦", "647", "F42", "135", "柒", "637", "㊆", "657", "❼"),
            ("KEY9", "8", "69", "⑧", "648", "　　", "134", "捌", "638", "㊇", "658", "❽"),
            ("KEY10", "9", "70", "⑨", "649", "F43", "136", "玖", "639", "㊈", "659", "❾"),
        ]
        for k_sec, digit, main_st, up_ch, up_st, dn_act, dn_st, left_ch, left_st, right_ch, right_st, hold_ch in num_grid_specs:
            if k_sec in sec_map:
                items = sec_map[k_sec]
                remove_sec_key(items, "SHOW")
                set_sec_val(items, "CENTER", digit)
                set_sec_val(items, "UP", up_ch)
                set_sec_val(items, "DOWN", dn_act)
                set_sec_val(items, "LEFT", left_ch)
                set_sec_val(items, "RIGHT", right_ch)
                set_sec_val(items, "HOLD", hold_ch)
                set_sec_val(items, "FORE_STYLE", f"{main_st},{dn_st},{up_st},{left_st},{right_st}")
                set_sec_val(items, "POS_TYPE", "61,63,64,65,62")

        # KEY11 (BACK): CENTER=F36, UP=F48, HOLD=F36 (no LEFT/RIGHT/DOWN -> native SlideDelete!)
        if "KEY11" in sec_map:
            set_sec_val(sec_map["KEY11"], "CENTER", "F36")
            set_sec_val(sec_map["KEY11"], "UP", "F48")
            remove_sec_key(sec_map["KEY11"], "DOWN")
            remove_sec_key(sec_map["KEY11"], "LEFT")
            remove_sec_key(sec_map["KEY11"], "RIGHT")
            set_sec_val(sec_map["KEY11"], "HOLD", "F36")

        # KEY13: = / % / ¥ (replaces broken Z+help)
        if "KEY13" in sec_map:
            set_sec_val(sec_map["KEY13"], "CENTER", "=")
            set_sec_val(sec_map["KEY13"], "UP", "%")
            set_sec_val(sec_map["KEY13"], "DOWN", "¥")
            set_sec_val(sec_map["KEY13"], "HOLD", "¥")
            set_sec_val(sec_map["KEY13"], "FORE_STYLE", "660,661,662")
            set_sec_val(sec_map["KEY13"], "POS_TYPE", "66,73,72")

        # KEY15 (SPACE): CENTER=F38, UP=F84, LEFT=F51 (L), RIGHT=F52 (R)
        if "KEY15" in sec_map:
            set_sec_val(sec_map["KEY15"], "FORE_STYLE", "74,626,141,142")
            set_sec_val(sec_map["KEY15"], "POS_TYPE", "66,79,81,82")

        # KEY16 (0): CENTER=0, UP=⑩(663), DOWN=F40(undo,144), LEFT=零(664), RIGHT=F52(R,142)
        if "KEY16" in sec_map:
            set_sec_val(sec_map["KEY16"], "CENTER", "0")
            set_sec_val(sec_map["KEY16"], "UP", "⑩")
            set_sec_val(sec_map["KEY16"], "DOWN", "F40")
            set_sec_val(sec_map["KEY16"], "LEFT", "零")
            set_sec_val(sec_map["KEY16"], "RIGHT", "F52")
            set_sec_val(sec_map["KEY16"], "HOLD", "F40")
            set_sec_val(sec_map["KEY16"], "FORE_STYLE", "71,144,663,664,142")
            set_sec_val(sec_map["KEY16"], "POS_TYPE", "61,63,64,65,62")

        # KEY17 (.): CENTER=., UP=，(665), DOWN=F37(del,145), LEFT=F51(L,141), RIGHT=F52(R,142)
        if "KEY17" in sec_map:
            set_sec_val(sec_map["KEY17"], "CENTER", ".")
            set_sec_val(sec_map["KEY17"], "UP", "，")
            set_sec_val(sec_map["KEY17"], "DOWN", "F37")
            set_sec_val(sec_map["KEY17"], "LEFT", "F51")
            set_sec_val(sec_map["KEY17"], "RIGHT", "F52")
            set_sec_val(sec_map["KEY17"], "HOLD", "F37")
            set_sec_val(sec_map["KEY17"], "FORE_STYLE", "72,145,665,141,142")
            set_sec_val(sec_map["KEY17"], "POS_TYPE", "61,63,64,81,82")

        if "KEY18" in sec_map:
            set_sec_val(sec_map["KEY18"], "FORE_STYLE", "917,624")
            set_sec_val(sec_map["KEY18"], "POS_TYPE", "66,71,72,73,74")
            set_sec_val(sec_map["KEY18"], "UP", "F76")

    if fl in {"en_26.ini", "en_26s.ini"}:
        patch_english_ascii_punctuation(sections)
    elif fl in {"py_26.ini", "def_26.ini"}:
        patch_chinese_26_punctuation(sections)
    add_hold_symbol_popups(sections)

    # 4. Renumber empty/gapped KEY and TIP sections contiguously and update STAT_STYLE & KEY_NUM / TIP_NUM
    has_key_or_tip = any(
        sec_name and re.match(r"^(KEY(_TOP_)?|TIP)\d+$", sec_name, re.I) for sec_name, _ in sections
    )
    if has_key_or_tip:
        filtered_sections = []
        old_to_new_tip = {}
        key_count = 0
        tip_count = 0

        for sec_name, items in sections:
            if sec_name and re.match(r"^KEY(_TOP_)?\d+$", sec_name, re.I):
                has_kv = any(k is not None and k.upper() != "INFO" for k, _ in items)
                if not has_kv:
                    continue
                key_count += 1
                filtered_sections.append([f"KEY{key_count}", items])
            elif sec_name and re.match(r"^TIP\d+$", sec_name, re.I):
                has_kv = any(k is not None and k.upper() != "INFO" for k, _ in items)
                if not has_kv:
                    continue
                old_idx = int(re.findall(r"\d+", sec_name)[0])
                tip_count += 1
                old_to_new_tip[old_idx] = tip_count
                filtered_sections.append([f"TIP{tip_count}", items])
            else:
                filtered_sections.append([sec_name, items])

        for sec_name, items in filtered_sections:
            if sec_name and sec_name.startswith("KEY"):
                stat_val = get_sec_val(items, "STAT_STYLE")
                if stat_val:
                    def _remap_stat(m):
                        st_code = m.group(1)
                        old_t = int(m.group(2))
                        new_t = old_to_new_tip.get(old_t, old_t)
                        return f"S{st_code}_{new_t}"
                    new_stat = re.sub(r"S(\d+)_(\d+)", _remap_stat, stat_val)
                    set_sec_val(items, "STAT_STYLE", new_stat)
            elif sec_name == "PANEL":
                set_sec_val(items, "KEY_NUM", str(key_count))
                set_sec_val(items, "TIP_NUM", str(tip_count))

        sections = filtered_sections

    return sections


def build_exact_keycaps_and_remap(theme_dir: Path, parsed_inis: dict):
    """
    1. Scan all KEY and TIP sections across port/*.ini and land/*.ini in `parsed_inis`.
    2. Collect every unique (base_style, W, H) where base_style in BASE_ANJIAN_STYLES.
    3. Assign a dedicated STYLE<id> (starting at 700) to each unique (base_style, W, H),
       and remap BACK_STYLE in every KEY (and its referenced TIPs) to that exact-size style.
    4. Generate pixel-perfect 9-sliced normal & pressed tiles of exact size (W, H) from
       res/anjian.png, pack them into an updated anjian.png + anjian.til (with SCALE=1,1,1,1,1),
       and return (png_bytes, til_text, extra_css_styles).
    """
    combo_to_style = {}
    combo_needs_hl = set()
    next_style_id = 700

    for rel_path, sections in sorted(parsed_inis.items()):
        sec_dict = {name: items for name, items in sections if name is not None}
        tip_wh = {}
        for sec_name, items in sections:
            if not sec_name or not sec_name.startswith("KEY"):
                continue
            vr = get_sec_val(items, "VIEW_RECT")
            if not vr:
                continue
            parts = [int(x.strip()) for x in vr.split(",")]
            if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
                continue
            w, h = parts[2], parts[3]

            stat = get_sec_val(items, "STAT_STYLE")
            if stat:
                for m in re.finditer(r"S\d+_(\d+)", stat):
                    t_idx = int(m.group(1))
                    tip_wh[t_idx] = (w, h)

            bs_str = get_sec_val(items, "BACK_STYLE")
            if bs_str and bs_str.isdigit():
                bs = int(bs_str)
                if bs in BASE_ANJIAN_STYLES:
                    combo = (bs, w, h)
                    if combo not in combo_to_style:
                        combo_to_style[combo] = next_style_id
                        next_style_id += 1
                    center_val = get_sec_val(items, "CENTER")
                    if center_val and center_val != "F53":
                        combo_needs_hl.add(combo)
                    set_sec_val(items, "BACK_STYLE", str(combo_to_style[combo]))

        for t_idx, (w, h) in tip_wh.items():
            t_name = f"TIP{t_idx}"
            if t_name in sec_dict:
                t_items = sec_dict[t_name]
                bs_str = get_sec_val(t_items, "BACK_STYLE")
                if bs_str and bs_str.isdigit():
                    bs = int(bs_str)
                    if bs in BASE_ANJIAN_STYLES:
                        combo = (bs, w, h)
                        if combo not in combo_to_style:
                            combo_to_style[combo] = next_style_id
                            next_style_id += 1
                        combo_needs_hl.add(combo)
                        set_sec_val(t_items, "BACK_STYLE", str(combo_to_style[combo]))

    orig_png = Image.open(theme_dir / "res" / "anjian.png").convert("RGBA")

    ATLAS_MAX_W = 2800
    cur_x = 0
    cur_y = 164
    row_h = 0
    placed_tiles = []

    next_img_idx = 17
    extra_css_blocks = []
    tiles_to_pack = []

    for (bs, w, h), style_id in sorted(combo_to_style.items(), key=lambda kv: kv[1]):
        nm_idx_orig, hl_idx_orig, sx, sy, sw, sh, t_cap, b_cap = BASE_ANJIAN_STYLES[bs]
        orig_nm = orig_png.crop((sx, sy, sx + sw, sy + sh))
        exact_nm = nine_slice_resize(orig_nm, w, h, left=28, right=28, top=t_cap, bottom=b_cap)

        nm_tile_id = next_img_idx
        next_img_idx += 1
        tiles_to_pack.append((nm_tile_id, w, h, exact_nm, t_cap, b_cap))

        if (bs, w, h) in combo_needs_hl:
            orig_hl = orig_png.crop((sx + 110, sy, sx + 110 + sw, sy + sh))
            exact_hl = nine_slice_resize(orig_hl, w, h, left=28, right=28, top=t_cap, bottom=b_cap)
            hl_tile_id = next_img_idx
            next_img_idx += 1
            tiles_to_pack.append((hl_tile_id, w, h, exact_hl, t_cap, b_cap))
        else:
            hl_tile_id = nm_tile_id

        extra_css_blocks.append(
            f"[STYLE{style_id}]\r\nNM_IMG=anjian,{nm_tile_id}\r\nHL_IMG=anjian,{hl_tile_id}"
        )

    for t_id, w, h, t_img, t_cap, b_cap in sorted(tiles_to_pack, key=lambda item: (item[2], item[1]), reverse=True):
        if cur_x + w > ATLAS_MAX_W and cur_x > 0:
            cur_x = 0
            cur_y += row_h + 4
            row_h = 0
        placed_tiles.append((t_id, cur_x, cur_y, w, h, t_img, t_cap, b_cap))
        cur_x += w + 4
        row_h = max(row_h, h)
    placed_tiles.sort(key=lambda item: item[0])

    total_h = max(py + ph for _, _, py, _, ph, _, _, _ in placed_tiles) + 4
    total_w = max(orig_png.width, ATLAS_MAX_W)
    atlas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    atlas.paste(orig_png, (0, 0))

    til_lines = [
        "[GLOBAL]",
        "USE_ALPHA=1",
        f"TILE_NUM={next_img_idx - 1}",
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
        ix = ox + 28
        iy = oy + tc
        iw = max(1, ow - 56)
        ih = max(1, oh - tc - bc)
        til_lines.extend([
            f"[IMG{idx}]",
            f"SOURCE_RECT={ox},{oy},{ow},{oh}",
            f"INNER_RECT={ix},{iy},{iw},{ih}",
            "SCALE=1,1,1,1,1",
            "",
        ])

    for t_id, px, py, pw, ph, t_img, tc, bc in placed_tiles:
        atlas.paste(t_img, (px, py))
        ix = px + min(28, pw // 3)
        iy = py + min(tc, ph // 3)
        iw = max(1, pw - 2 * min(28, pw // 3))
        ih = max(1, ph - min(tc, ph // 3) - min(bc, ph // 3))
        til_lines.extend([
            f"[IMG{t_id}]",
            f"SOURCE_RECT={px},{py},{pw},{ph}",
            f"INNER_RECT={ix},{iy},{iw},{ih}",
            "SCALE=1,1,1,1,1",
            "",
        ])

    buf = io.BytesIO()
    atlas.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), "\r\n".join(til_lines) + "\r\n", "\r\n\r\n".join(extra_css_blocks)


def patch_default_css_android(text: str, is_dark: bool, extra_anjian_css: str = "") -> str:
    """
    1. Keep native Android .ogg sound references (STYLE300=aj.ogg, STYLE301=b.ogg).
    2. Update STYLE620..627 from sym,21..28 -> sym,12..19 (matching contiguous sym.til).
    3. Deduplicate duplicate [STYLE...] sections and duplicate keys inside sections.
    4. Add STYLE144..149 (undo, redo, clear, up, down, clip), STYLE247..249 (logo.ini), and extra_anjian_css.
    """
    for old_idx in range(21, 29):
        new_idx = old_idx - 9
        text = re.sub(rf"\bsym,{old_idx}\b", f"sym,{new_idx}", text)
        text = re.sub(rf"\bsym2,{old_idx}\b", f"sym2,{new_idx}", text)

    sections = parse_ini_sections(text)
    deduped_sections = []
    seen_sec_names = set()
    for sec_name, items in sections:
        cleaned_items = dedup_sec_keys(items)
        if sec_name is not None:
            su = sec_name.upper()
            has_kv = any(k is not None and k.upper() != "INFO" for k, _ in cleaned_items)
            if su == "STYLE149" and not has_kv:
                continue
            if su in {"STYLE135", "STYLE136"}:
                set_sec_val(cleaned_items, "FONT_SIZE", "14")
            if su in seen_sec_names:
                continue
            seen_sec_names.add(su)
        deduped_sections.append([sec_name, cleaned_items])
    text = serialize_ini_sections(deduped_sections)

    nm_sub = "8c8c8c" if is_dark else "000000"
    nm_main = "ebebeb" if is_dark else "333333"
    hl_sub = "ebebeb" if is_dark else "ffffff"

    gesture_styles = []
    for st_id, label, custom_nm, fsize in [
        (144, "undo", nm_sub, 20),
        (145, "del", nm_sub, 20),
        (146, "clear", nm_sub, 20),
        (147, "up", nm_sub, 20),
        (148, "down", nm_sub, 20),
        (149, "clip", "ffffff", 20),
        (170, "、", nm_sub, 20),
        (171, "…", nm_sub, 20),
        (172, "“", nm_sub, 20),
        (173, "”", nm_sub, 20),
        (174, "—", nm_sub, 20),
        (175, "《", nm_sub, 20),
        (176, "》", nm_sub, 20),
        (177, "@", nm_sub, 20),
        *[(style_id, label, nm_sub, 20) for style_id, label in ENGLISH_SYMBOL_STYLES.items()],
        *[(style_id, label, nm_sub, 20) for style_id, label in CHINESE_26_SYMBOL_STYLES.items()],
        (630, "sel", nm_sub, 18),
        (631, "壹", nm_sub, 18),
        (632, "贰", nm_sub, 18),
        (633, "叁", nm_sub, 18),
        (634, "肆", nm_sub, 18),
        (635, "伍", nm_sub, 18),
        (636, "陆", nm_sub, 18),
        (637, "柒", nm_sub, 18),
        (638, "捌", nm_sub, 18),
        (639, "玖", nm_sub, 18),
        (641, "①", nm_sub, 20),
        (642, "②", nm_sub, 20),
        (643, "③", nm_sub, 20),
        (644, "④", nm_sub, 20),
        (645, "⑤", nm_sub, 20),
        (646, "⑥", nm_sub, 20),
        (647, "⑦", nm_sub, 20),
        (648, "⑧", nm_sub, 20),
        (649, "⑨", nm_sub, 20),
        (651, "㊀", nm_sub, 18),
        (652, "㊁", nm_sub, 18),
        (653, "㊂", nm_sub, 18),
        (654, "㊃", nm_sub, 18),
        (655, "㊄", nm_sub, 18),
        (656, "㊅", nm_sub, 18),
        (657, "㊆", nm_sub, 18),
        (658, "㊇", nm_sub, 18),
        (659, "㊈", nm_sub, 18),
        (660, "=", nm_main, 42),
        (661, "%", nm_sub, 20),
        (662, "¥", nm_sub, 20),
        (663, "⑩", nm_sub, 20),
        (664, "零", nm_sub, 18),
        (665, "，", nm_sub, 20),
        (666, ",", nm_main, 42),
    ]:
        if f"[STYLE{st_id}]" not in text:
            gesture_styles.append(
                f"[STYLE{st_id}]\r\nSHOW={label}\r\nNM_COLOR={custom_nm}\r\nHL_COLOR={hl_sub}\r\n"
                f"FONT_SIZE={fsize}\r\nBORDER_COLOR=AFFFFFFF\r\nBORDER_SIZE=1"
            )

    if is_dark:
        logo_styles = (
            "[STYLE247]\r\nNM_COLOR=222222\r\nHL_COLOR=222222\r\n\r\n"
            "[STYLE248]\r\nNM_COLOR=2c2c2e\r\nHL_COLOR=3a3a3c\r\n\r\n"
            "[STYLE249]\r\nFONT_SIZE=36\r\nNM_COLOR=ebebeb\r\nHL_COLOR=ed5858"
        )
    else:
        logo_styles = (
            "[STYLE247]\r\nNM_COLOR=ededed\r\nHL_COLOR=ededed\r\n\r\n"
            "[STYLE248]\r\nNM_COLOR=ffffff\r\nHL_COLOR=e5e5e5\r\n\r\n"
            "[STYLE249]\r\nFONT_SIZE=36\r\nNM_COLOR=333333\r\nHL_COLOR=f75757"
        )

    appended = [text.rstrip()]
    if gesture_styles:
        appended.append("\r\n\r\n".join(gesture_styles))
    if "[STYLE247]" not in text:
        appended.append(logo_styles)
    if extra_anjian_css:
        appended.append(extra_anjian_css)

    return "\r\n\r\n".join(appended) + "\r\n"


def write_android_theme_tree(zf: zipfile.ZipFile, src_theme_dir: Path, zip_prefix: str, is_dark: bool):
    """
    Write one theme tree into zip_prefix ('light' or 'dark') for Android .bds.
    """
    parsed_inis = {}
    for orientation in ["port", "land"]:
        ori_dir = src_theme_dir / orientation
        if not ori_dir.exists():
            continue
        for fpath in sorted(ori_dir.iterdir()):
            if fpath.is_file() and fpath.suffix.lower() == ".ini":
                rel_path = f"{orientation}/{fpath.name}"
                raw_text = decode_text_clean(fpath.read_bytes())
                if fpath.name.lower() == "gen.ini":
                    patched_gen = patch_gen_ini_android(orientation, raw_text)
                    parsed_inis[rel_path] = parse_ini_sections(patched_gen)
                else:
                    parsed_inis[rel_path] = patch_layout_ini_android(orientation, fpath.name, raw_text)
                    if fpath.name.lower() == "py_9.ini":
                        parsed_inis[f"{orientation}/py_14.ini"] = patch_layout_ini_android(
                            orientation, "py_14.ini", raw_text
                        )

    anjian_png_bytes, anjian_til_text, extra_anjian_css = build_exact_keycaps_and_remap(src_theme_dir, parsed_inis)
    sym_nm_bytes, sym_hl_bytes, sym_til_text = build_sym_assets(src_theme_dir)

    for root, dirs, files in os.walk(src_theme_dir):
        rel_root = Path(root).relative_to(src_theme_dir).as_posix()
        for fname in sorted(files):
            fpath = Path(root) / fname
            rel_path = f"{rel_root}/{fname}" if rel_root != "." else fname
            arc_path = f"{zip_prefix}/{rel_path}"
            fl = fname.lower()
            rl = rel_path.lower()

            if rl == "info.txt":
                continue

            if rl == "res/anjian.png":
                zf.writestr(arc_path, anjian_png_bytes)
                continue
            if rl == "res/anjian.til":
                zf.writestr(arc_path, anjian_til_text.encode("utf-8"))
                continue
            if rl == "res/sym.png":
                zf.writestr(arc_path, sym_nm_bytes)
                continue
            if rl == "res/sym2.png":
                zf.writestr(arc_path, sym_hl_bytes)
                continue
            if rl in {"res/sym.til", "res/sym2.til"}:
                zf.writestr(arc_path, sym_til_text.encode("utf-8"))
                continue

            if rel_path in parsed_inis:
                ini_text = serialize_ini_sections(parsed_inis[rel_path])
                zf.writestr(arc_path, ini_text.encode("utf-8"))
                continue

            ext = fpath.suffix.lower()
            if ext in TEXT_EXTS:
                text = decode_text_clean(fpath.read_bytes())
                if fl == "default.css":
                    text = patch_default_css_android(text, is_dark=is_dark, extra_anjian_css=extra_anjian_css)
                elif fl == "hint1.pop":
                    pop_secs = parse_ini_sections(text)
                    for sname, sitems in pop_secs:
                        if sname == "HINT":
                            set_sec_val(sitems, "BACK_ICON", "1")
                    text = serialize_ini_sections(pop_secs)
                zf.writestr(arc_path, text.encode("utf-8"))
            else:
                zf.write(fpath, arc_path)

    for extra_rel in ("port/py_14.ini", "land/py_14.ini"):
        if extra_rel in parsed_inis:
            ini_text = serialize_ini_sections(parsed_inis[extra_rel])
            zf.writestr(f"{zip_prefix}/{extra_rel}", ini_text.encode("utf-8"))


def build_dual_bds(src_dir: Path, out_bds: Path, skin_name: str):
    info_txt = make_android_info_txt(skin_name, dual_mode=True, has_sound=True)
    out_bds.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_bds, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("Info.txt", info_txt.encode("utf-8"))
        demo_path = src_dir / "demo.png"
        if demo_path.exists():
            zf.write(demo_path, "demo.png")

        write_android_theme_tree(zf, src_dir / "light", "light", is_dark=False)
        write_android_theme_tree(zf, src_dir / "dark", "dark", is_dark=True)
    print(f"[OK] Built dual-mode Android skin: {out_bds} ({out_bds.stat().st_size:,} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the dual-mode Android phone skin.")
    parser.add_argument("--source-dir", type=Path, default=EXTRACTED_DIR / "dark_14",
                        help="Base theme tree containing light/, dark/ and demo.png")
    parser.add_argument("--output", type=Path, default=OUT_DIR / "森林集-全能Pro.bds")
    args = parser.parse_args()
    if not all((args.source_dir / theme).is_dir() for theme in ("light", "dark")):
        parser.error("Missing light/ or dark/ source tree; use the complete release archive or --source-dir.")
    build_dual_bds(args.source_dir, args.output, "森林集-全能Pro")
