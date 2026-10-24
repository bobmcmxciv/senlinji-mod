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
EXTRACTED_DIR = BUILD_DIR / "extracted"

AJ_AIFF = BUILD_DIR / "aj.aiff"
B_AIFF = BUILD_DIR / "b.aiff"

TEXT_EXTS = {".ini", ".cnd", ".css", ".til", ".txt"}

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


class StyleRegistry:
    """
    Allocates deterministic [STYLE<id>] blocks in default.css for crisp iOS native text labels.
    Every keycap text label ('center', 'up', 'down', 'left', 'right') maps 1-to-1 to a style ID.
    Available free ranges in default.css: 310..399, 630..699, 930..1499.
    """

    def __init__(self):
        self.pool = (
            list(range(310, 400))
            + list(range(630, 700))
            + list(range(930, 1500))
        )
        self.key_to_id = {}
        self.id_to_spec = {}

    def get(self, text: str, kind: str = "sub") -> int:
        """
        kind:
          - 'sub'       : FONT_SIZE=20 (top/bottom/left/right sub-label)
          - 'sub_white' : FONT_SIZE=20, white (top sub-label on coral-red ENTER key)
          - 'main_lg'   : FONT_SIZE=46 (1-char main label: kana, cyrillic, digit, symbol)
          - 'main_md'   : FONT_SIZE=34 (2-3 char main label: EN, JP, CH, 123, SYM, sym, 返回, 濁音゛, ア片, АБВ)
          - 'main_sm'   : FONT_SIZE=30 (4-5 char main label: SPACE, BACK)
          - 'main_enter': FONT_SIZE=30, white (ENTER on coral-red key)
        """
        key = (text, kind)
        if key in self.key_to_id:
            return self.key_to_id[key]
        if not self.pool:
            raise RuntimeError("StyleRegistry pool exhausted!")
        st_id = self.pool.pop(0)
        self.key_to_id[key] = st_id
        self.id_to_spec[st_id] = (text, kind)
        return st_id

    def emit_css_blocks(self, is_dark: bool) -> str:
        nm_main = "ebebeb" if is_dark else "222222"
        nm_sub = "9a9a9e" if is_dark else "333333"
        hl_all = "ffffff"

        blocks = []
        for st_id in sorted(self.id_to_spec.keys()):
            text, kind = self.id_to_spec[st_id]
            if kind == "sub":
                fsize, nm, hl = 20, nm_sub, hl_all
            elif kind == "sub_white":
                fsize, nm, hl = 20, "ffffff", "ffffff"
            elif kind == "main_lg":
                fsize, nm, hl = 46, nm_main, hl_all
            elif kind == "main_md":
                fsize, nm, hl = 34, nm_main, hl_all
            elif kind == "main_sm":
                fsize, nm, hl = 30, nm_main, hl_all
            elif kind == "main_enter":
                fsize, nm, hl = 30, "ffffff", "ffffff"
            else:
                fsize, nm, hl = 20, nm_sub, hl_all

            blocks.append(
                f"[STYLE{st_id}]\r\n"
                f"SHOW={text}\r\n"
                f"NM_COLOR={nm}\r\n"
                f"HL_COLOR={hl}\r\n"
                f"FONT_SIZE={fsize}\r\n"
                f"FONT_CLEARTYPE=1"
            )
        return "\r\n\r\n".join(blocks)


def decode_text_clean(raw: bytes) -> str:
    """Decode UTF-8 (with or without BOM) or GBK/GB18030, then scrub any corrupted U+FFFD lines."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gb18030")
    lines = []
    for raw_line in text.rstrip("\r\n").split("\n"):
        line = raw_line.rstrip("\r")
        if "\ufffd" in line:
            if line.strip(" \t\r\n").startswith("INFO="):
                line = "INFO=微信公众号----森林集"
            else:
                line = line.replace("\ufffd", "")
        lines.append(line)
    return "\r\n".join(lines) + "\r\n"


def parse_ini_sections(text: str):
    """
    Parse an INI/CND/TIL/CSS text into an ordered list of (section_name_or_None, [(key_or_None, raw_line_or_val)]).
    Note: Uses split('\\n') and strip(' \\t\\r\\n') so U+2028 (Unicode Line Separator) is preserved intact.
    """
    sections = []
    cur_name = None
    cur_items = []
    for raw_line in text.rstrip("\r\n").split("\n"):
        raw_line = raw_line.rstrip("\r")
        s = raw_line.strip(" \t\r\n")
        if s.startswith("[") and s.endswith("]"):
            if cur_name is not None or cur_items:
                sections.append([cur_name, cur_items])
            cur_name = s[1:-1].strip(" \t\r\n")
            cur_items = []
        elif "=" in raw_line and not s.startswith(";") and not s.startswith("#"):
            k, v = raw_line.split("=", 1)
            cur_items.append([k.strip(" \t\r\n"), v.strip(" \t\r\n")])
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


def make_info_txt(name: str, dual_mode: bool, has_sound: bool = True) -> str:
    lines = [
        f"Name={name}",
        "SupportPlatform=I",
        "SkinFlags=1",
        "SkinType=1",
    ]
    if dual_mode:
        lines.append("SupportDarkMode=1")
    if has_sound:
        lines.append("Abilities=Sound")
    return "\r\n".join(lines) + "\r\n"


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


def build_sym_assets(theme_dir: Path, is_dark: bool):
    """Emit contiguous sym.til / sym2.til (IMG1..19, TILE_NUM=19)."""
    out_nm = Image.open(theme_dir / "res" / "sym.png").convert("RGBA")
    out_hl = Image.open(theme_dir / "res" / "sym2.png").convert("RGBA")

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
        "SCALE=1,1,1,1,1",
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


def patch_other_til(fname: str, text: str) -> str:
    """Ensure any .til section with INNER_RECT also has SCALE=1,1,1,1,1, and fix help.til."""
    sections = parse_ini_sections(text)
    for sec_name, items in sections:
        if sec_name and sec_name.upper().startswith("IMG"):
            if fname.lower() == "help.til":
                src_rect = get_sec_val(items, "SOURCE_RECT")
                if src_rect:
                    parts = [int(x) for x in src_rect.split(",")]
                    set_sec_val(items, "INNER_RECT", f"{parts[2]//2},{parts[3]//2},1,1")
                    set_sec_val(items, "SCALE", "1,1,1,1,1")
            elif get_sec_val(items, "INNER_RECT") is not None and get_sec_val(items, "SCALE") is None:
                set_sec_val(items, "SCALE", "1,1,1,1,1")
    return serialize_ini_sections(sections)


def patch_cand_cnd(orientation: str, text: str) -> str:
    if orientation == "port":
        lines = [
            "[CAND]",
            "BACK_STYLE=200",
            "FORE_STYLE=150",
            "PADDING=24,6,162,0",
            "FIRST_GAP=24",
            "FIRST_FORE=151",
            "CELL_W=66",
            "ICON_NUM=4",
            "TIP_NUM=0",
            "",
            "[SWITCH]",
            "NML_BACK_STYLE=",
            "SEL_BACK_STYLE=",
            "NML_FONT_STYLE=158",
            "SEL_FONT_STYLE=159",
            "PADDING=0",
            "",
            "[ICON1]",
            "FORE_STYLE=400",
            "SIZE=174,114",
            "FIX_SIZE=45,37",
            "ANCHOR_TYPE=4",
            "POS=0,-51",
            "KEY=F31",
            "PERSIST=1",
            "",
            "[ICON2]",
            "SIZE=789,114",
            "ANCHOR_TYPE=5",
            "POS=-394,-51",
            "KEY=F14",
            "PERSIST=1",
            "",
            "[ICON3]",
            "FORE_STYLE=401",
            "SIZE=162,114",
            "FIX_SIZE=45,37",
            "ANCHOR_TYPE=6",
            "POS=-162,-54",
            "KEY=F8",
            "PERSIST=1",
            "",
            "[ICON4]",
            "FORE_STYLE=410",
            "SIZE=162,114",
            "FIX_SIZE=45,37",
            "ANCHOR_TYPE=6",
            "POS=-162,-55",
            "KEY=F9",
            "PERSIST=2",
        ]
    else:
        lines = [
            "[CAND]",
            "BACK_STYLE=200",
            "FORE_STYLE=150",
            "PADDING=36,8,179,0",
            "FIRST_GAP=32",
            "FIRST_FORE=151",
            "CELL_W=112",
            "ICON_NUM=4",
            "TIP_NUM=0",
            "",
            "[SWITCH]",
            "NML_BACK_STYLE=",
            "SEL_BACK_STYLE=",
            "NML_FONT_STYLE=158",
            "SEL_FONT_STYLE=159",
            "PADDING=0",
            "",
            "[ICON1]",
            "FORE_STYLE=400",
            "SIZE=275,139",
            "FIX_SIZE=45,37",
            "ANCHOR_TYPE=4",
            "POS=0,-69",
            "KEY=F31",
            "PERSIST=1",
            "",
            "[ICON2]",
            "SIZE=1370,139",
            "ANCHOR_TYPE=5",
            "POS=-685,-69",
            "KEY=F14",
            "PERSIST=1",
            "",
            "[ICON3]",
            "FORE_STYLE=401",
            "SIZE=275,139",
            "FIX_SIZE=45,37",
            "ANCHOR_TYPE=6",
            "POS=-275,-69",
            "KEY=F8",
            "PERSIST=1",
            "",
            "[ICON4]",
            "FORE_STYLE=410",
            "SIZE=179,139",
            "FIX_SIZE=45,37",
            "ANCHOR_TYPE=6",
            "POS=-179,-69",
            "KEY=F9",
            "PERSIST=2",
        ]
    return "\r\n".join(lines) + "\r\n"


def patch_gen_ini(orientation: str, text: str) -> str:
    """
    Configure gen.ini with clean, exact OFFSET coordinates for all key widths:
      OFFSET1   : Center (0,-2)
      OFFSET2   : Top-Center (0,-44 port / 0,-59 land)
      OFFSET3   : Bottom-Center (0,38 port / 0,51 land)
      OFFSET4   : Left on narrow 100..115px key (-33,0 port / -56,0 land)
      OFFSET5   : Right on narrow 100..115px key (35,0 port / 59,0 land)
      OFFSET140 : Left main letter on 14-key (-23,-2 port / -39,-2 land)
      OFFSET141 : Right main letter on 14-key (23,-2 port / 39,-2 land)
      OFFSET142 : Top-Center on 14-key (0,-44 port / 0,-59 land)
      OFFSET143 : Bottom-Center on 14-key (0,38 port / 0,51 land)
      OFFSET144 : Left on 221px 14-key GH (-64,0 port / -105,0 land)
      OFFSET145 : Right on 221px 14-key GH (64,0 port / 105,0 land)
      OFFSET146 : Left on 179px sidebar key (-56,0 port / -94,0 land)
      OFFSET147 : Right on 179px sidebar key (56,0 port / 94,0 land)
      OFFSET148 : Left on 253px 9-key cell (-84,0 port / -142,0 land)
      OFFSET149 : Right on 253px 9-key cell (84,0 port / 142,0 land)
      OFFSET150 : Left on 448px wide SPACE (-180,0 port / -305,0 land)
      OFFSET151 : Right on 448px wide SPACE (180,0 port / 305,0 land)
    """
    sections = parse_ini_sections(text)
    has_scand = False
    has_logo = False

    is_port = orientation == "port"
    extra_offsets = {
        "OFFSET1": "0,-2",
        "OFFSET2": "0,-44" if is_port else "0,-59",
        "OFFSET3": "0,38" if is_port else "0,51",
        "OFFSET4": "-33,0" if is_port else "-56,0",
        "OFFSET5": "35,0" if is_port else "59,0",
        "OFFSET140": "-23,-2" if is_port else "-39,-2",
        "OFFSET141": "23,-2" if is_port else "39,-2",
        "OFFSET142": "0,-44" if is_port else "0,-59",
        "OFFSET143": "0,38" if is_port else "0,51",
        "OFFSET144": "-64,0" if is_port else "-105,0",
        "OFFSET145": "64,0" if is_port else "105,0",
        "OFFSET146": "-56,0" if is_port else "-94,0",
        "OFFSET147": "56,0" if is_port else "94,0",
        "OFFSET148": "-84,0" if is_port else "-142,0",
        "OFFSET149": "84,0" if is_port else "142,0",
        "OFFSET150": "-180,0" if is_port else "-305,0",
        "OFFSET151": "180,0" if is_port else "305,0",
    }
    seen_offsets = set()

    for sec_name, items in sections:
        if sec_name == "PANEL":
            set_sec_val(items, "OFFSET_NUM", "200")
        elif sec_name == "MORE":
            set_sec_val(items, "GRID", "4,5")
            set_sec_val(items, "LAYOUT_NAME", "sel_ch")
            set_sec_val(items, "SYM_LAYOUT", "symbol")
            set_sec_val(items, "FORE_STYLE", "154")
        elif sec_name == "LIST":
            set_sec_val(items, "LIST_ORDER", "0")
            remove_sec_key(items, "NAMES")
            remove_sec_key(items, "VALUES")
        elif sec_name == "SCAND":
            has_scand = True
            set_sec_val(items, "BACK_STYLE", "922")
            set_sec_val(items, "INPUT_STYLE", "157")
            set_sec_val(items, "FORE_STYLE", "157")
        elif sec_name == "LOGO":
            has_logo = True
            set_sec_val(items, "LAYOUT_NAME", "logo")
        elif sec_name in extra_offsets:
            seen_offsets.add(sec_name)
            set_sec_val(items, "POS", extra_offsets[sec_name])

    for off_name, pos_val in extra_offsets.items():
        if off_name not in seen_offsets:
            sections.append([off_name, [["POS", pos_val]]])
    if not has_scand:
        sections.append(["SCAND", [["BACK_STYLE", "922"], ["INPUT_STYLE", "157"], ["FORE_STYLE", "157"]]])
    if not has_logo:
        sections.append(["LOGO", [["LAYOUT_NAME", "logo"]]])
    return serialize_ini_sections(sections)


def _set_clean_key(items, center=None, up=None, down=None, left=None, right=None, hold=None, show=None, fore_style=None, pos_type=None, stat_style=None):
    """Helper to set a key's actions and styles while stripping any stale/hidden attributes."""
    if center is not None:
        set_sec_val(items, "CENTER", center)
    if up is None:
        remove_sec_key(items, "UP")
    else:
        set_sec_val(items, "UP", up)
    if down is None:
        remove_sec_key(items, "DOWN")
    else:
        set_sec_val(items, "DOWN", down)
    if left is None:
        remove_sec_key(items, "LEFT")
    else:
        set_sec_val(items, "LEFT", left)
    if right is None:
        remove_sec_key(items, "RIGHT")
    else:
        set_sec_val(items, "RIGHT", right)
    if hold is None:
        remove_sec_key(items, "HOLD")
    else:
        set_sec_val(items, "HOLD", hold)
    if show is None:
        remove_sec_key(items, "SHOW")
    else:
        set_sec_val(items, "SHOW", show)
    if fore_style is not None:
        set_sec_val(items, "FORE_STYLE", fore_style)
    if pos_type is not None:
        set_sec_val(items, "POS_TYPE", pos_type)
    if stat_style is None:
        remove_sec_key(items, "STAT_STYLE")
    elif stat_style != "__KEEP__":
        set_sec_val(items, "STAT_STYLE", stat_style)


def patch_layout_ini_pre_sizing(orientation: str, fname: str, text: str, reg: StyleRegistry):
    """
    Apply all functional, gesture, and 100%-label-matched layout fixes to an INI file
    BEFORE we collect exact (BACK_STYLE, W, H) sizes and normalize section numbers.
    """
    fl = fname.lower()
    sections = parse_ini_sections(text)

    for sec in sections:
        sec[1] = dedup_sec_keys(sec[1])

    sec_map = {sec_name: items for sec_name, items in sections if sec_name is not None}

    # Common style IDs from StyleRegistry
    st_nl_w = reg.get("换行", "sub_white")
    st_clear = reg.get("clear", "sub")
    st_tab = reg.get("tab", "sub")
    st_home = reg.get("home", "sub")
    st_end = reg.get("end", "sub")
    st_up = reg.get("↑", "sub")
    st_dn = reg.get("↓", "sub")
    st_lt = reg.get("←", "sub")
    st_rt = reg.get("→", "sub")
    st_dun = reg.get("、", "sub")
    st_fen = reg.get("；", "sub")
    st_dash = reg.get("—", "sub")
    st_ellip = reg.get("…", "sub")
    st_tilde = reg.get("~", "sub")
    st_at = reg.get("@", "sub")
    st_mid = reg.get("·", "sub")
    st_eq = reg.get("=", "sub")
    st_plus = reg.get("+", "sub")
    st_hash = reg.get("#", "sub")
    st_emoji = reg.get("表情", "sub")
    st_sep = reg.get("'", "sub")
    st_sym_sub = reg.get("sym", "sub")
    st_slash = reg.get("/", "sub")
    st_pct = reg.get("%", "sub")
    s_colon = reg.get("：", "sub")

    st_main_sym_lc = reg.get("sym", "main_md")
    st_main_sym_uc = reg.get("SYM", "main_md")
    st_main_quot = reg.get("'", "main_lg")
    st_main_back = reg.get("BACK", "main_sm")
    st_main_123 = reg.get("123", "main_md")
    st_main_en = reg.get("EN", "main_md")
    st_main_ch = reg.get("CH", "main_md")
    st_main_comma_cn = reg.get("，", "main_lg")
    st_main_period_cn = reg.get("。", "main_lg")
    st_main_comma_en = reg.get(",", "main_lg")
    st_main_period_en = reg.get(".", "main_lg")
    st_main_space = reg.get("SPACE", "main_sm")
    st_main_enter = reg.get("ENTER", "main_enter")

    # =========================================================================
    # 1. 14-Key Layouts (py_9.ini, py_14.ini, def_9.ini)
    # =========================================================================
    if fl in {"py_9.ini", "py_14.ini", "def_9.ini"}:
        # Row 1 (QW, ER, TY, UI, OP):
        #   UP   = 1, 2, 3, 4, 5
        #   DOWN = —, …, ~, clear(F48), tab(F41)
        # Row 2 (AS, DF, GH, JK, L):
        #   AS: UP=6, DOWN=@
        #   DF: UP=7, DOWN=、
        #   GH: UP=8, DOWN=·
        #   JK: UP=9, DOWN=；
        #   L : UP=0, DOWN=/
        # Row 3 (ZX, CV, BN, M):
        #   UP = ！, ：, %, ？ ; NO DOWN= gestures!
        s_exclam = reg.get("！", "sub")
        s_quest = reg.get("？", "sub")

        letter_14_specs = {
            "KEY1": ("q", "w", "q", "w", "1", "—", f"1,2,100,{st_dash}", "140,141,142,143"),
            "KEY2": ("e", "r", "r", "e", "2", "…", f"3,4,101,{st_ellip}", "140,141,142,143"),
            "KEY3": ("t", "y", "t", "y", "3", "~", f"5,6,102,{st_tilde}", "140,141,142,143"),
            "KEY4": ("u", "i", "i", "u", "4", "F48", f"7,8,103,{st_clear}", "140,141,142,143"),
            "KEY5": ("o", "p", "p", "o", "5", "F41", f"9,10,104,{st_tab}", "140,141,142,143"),
            "KEY11": ("a", "s", "s", "a", "6", "@", f"11,12,105,{st_at}", "140,141,142,143"),
            "KEY12": ("d", "f", "d", "f", "7", "、", f"13,14,106,{st_dun}", "140,141,142,143"),
            "KEY13": ("g", "h", "g", "h", "8", "·", f"15,16,107,{st_mid}", "140,141,142,143"),
            "KEY14": ("j", "k", "j", "k", "9", "；", f"17,18,108,{st_fen}", "140,141,142,143"),
            "KEY15": ("l", "l", "l", "l", "0", "/", f"19,109,{st_slash}", "1,2,3"),
            "KEY20": ("z", "x", "x", "z", "！", None, f"20,21,{s_exclam}", "140,141,142"),
            "KEY21": ("c", "v", "v", "c", "：", None, f"22,23,{s_colon}", "140,141,142"),
            "KEY22": ("b", "n", "b", "n", "%", None, f"24,25,{st_pct}", "140,141,142"),
            "KEY23": ("m", "m", "m", "m", "？", None, f"26,{s_quest}", "1,2"),
        }
        for k_sec, (l_ch, r_ch, c_ch, s_ch, up_act, dn_act, f_style, p_type) in letter_14_specs.items():
            if k_sec in sec_map:
                hold_act = dn_act if dn_act else None
                _set_clean_key(
                    sec_map[k_sec],
                    center=c_ch,
                    up=up_act,
                    down=dn_act,
                    left=l_ch,
                    right=r_ch,
                    hold=hold_act,
                    show=s_ch,
                    fore_style=f_style,
                    pos_type=p_type,
                    stat_style="__KEEP__",
                )

        # sym (KEY27): Center='sym' (F1), Up='sym' (F1), Down=''' (' separator)
        if "KEY27" in sec_map:
            _set_clean_key(
                sec_map["KEY27"],
                center="F1",
                up="F1",
                down="'",
                hold="'",
                fore_style=f"{st_main_sym_lc},{st_sym_sub},{st_sep}",
                pos_type="1,2,3",
                stat_style="S4_2",
            )
        if "TIP1" in sec_map:
            _set_clean_key(
                sec_map["TIP1"],
                center="F1",
                up="F1",
                down="'",
                fore_style=f"{st_main_sym_lc},{st_sym_sub},{st_sep}",
                pos_type="1,2,3",
            )
        if "TIP2" in sec_map:
            _set_clean_key(
                sec_map["TIP2"],
                center="'",
                up="F1",
                down="'",
                fore_style=f"{st_main_quot},{st_sym_sub},{st_sep}",
                pos_type="1,2,3",
            )

        # BACK (KEY28): Center=BACK(F36), Up=clear(F48) (NO DOWN=del!)
        if "KEY28" in sec_map:
            _set_clean_key(
                sec_map["KEY28"],
                center="F36",
                up="F48",
                down=None,
                hold="F36",
                fore_style=f"{st_main_back},{st_clear}",
                pos_type="1,2",
            )

        # 123 (KEY29): Center=123(F6), Up=表情(F7)
        if "KEY29" in sec_map:
            _set_clean_key(
                sec_map["KEY29"],
                center="F6",
                up="F7",
                hold="F7",
                fore_style=f"{st_main_123},{st_emoji}",
                pos_type="1,2",
            )

        # EN (KEY30): Tap toggles CH<->EN (F16); no UP/DOWN/HOLD
        if "KEY30" in sec_map:
            _set_clean_key(
                sec_map["KEY30"],
                center="F16",
                up=None,
                down=None,
                hold=None,
                fore_style=f"{st_main_en}",
                pos_type="1",
            )

        # ， (KEY31): Center=，, Up=home(F42)
        if "KEY31" in sec_map:
            _set_clean_key(
                sec_map["KEY31"],
                center="，",
                up="F42",
                hold="F42",
                fore_style=f"{st_main_comma_cn},{st_home}",
                pos_type="1,2",
            )

        # SPACE (KEY32): Center=SPACE(F38), Left=←(F51), Right=→(F52) (removed unsupported 常用语 F77)
        if "KEY32" in sec_map:
            _set_clean_key(
                sec_map["KEY32"],
                center="F38",
                up=None,
                left="F51",
                right="F52",
                fore_style=f"{st_main_space},{st_lt},{st_rt}",
                pos_type="1,150,151",
            )

        # 。 (KEY33): Center=。, Up=end(F43)
        if "KEY33" in sec_map:
            _set_clean_key(
                sec_map["KEY33"],
                center="。",
                up="F43",
                hold="F43",
                fore_style=f"{st_main_period_cn},{st_end}",
                pos_type="1,2",
            )

        # ENTER (KEY34): Center=ENTER(F39), Up=换行(\u2028 soft newline without sending)
        if "KEY34" in sec_map:
            _set_clean_key(
                sec_map["KEY34"],
                center="F39",
                up="\u2028",
                hold="\u2028",
                fore_style=f"{st_main_enter},{st_nl_w}",
                pos_type="1,2",
            )

    # =========================================================================
    # 2. 26-Key Layouts (py_26.ini, def_26.ini, en_26.ini, en_26s.ini)
    # =========================================================================
    if fl in {"py_26.ini", "def_26.ini", "en_26.ini", "en_26s.ini"}:
        is_en26 = fl in {"en_26.ini", "en_26s.ini"}
        st_lquote = reg.get("「", "sub")
        st_rquote = reg.get("」", "sub")
        st_rmb = reg.get("¥", "sub")
        st_pct = reg.get("%", "sub")
        st_lbook = reg.get("《", "sub")
        st_rbook = reg.get("》", "sub")
        st_dollar = reg.get("$", "sub")

        # Row 1 (KEY1..10 & TIP11..20):
        # Q=…, W=·, E==, R=clear(F48), T=tab(F41),
        # Y=、, U=；, I=—, O=「, P=」
        r1_26_specs = [
            (1, "q", "1", "…", 100, st_ellip),
            (2, "w", "2", "·", 101, st_mid),
            (3, "e", "3", "=", 102, st_eq),
            (4, "r", "4", "F48", 103, st_clear),
            (5, "t", "5", "F41", 104, st_tab),
            (6, "y", "6", "、", 105, st_dun),
            (7, "u", "7", "；", 106, st_fen),
            (8, "i", "8", "—", 107, st_dash),
            (9, "o", "9", "「", 108, st_lquote),
            (10, "p", "0", "」", 109, st_rquote),
        ]
        for idx, ch, up_v, dn_v, up_st, dn_st in r1_26_specs:
            k_sec = f"KEY{idx}"
            t_sec = f"TIP{idx + 10}"
            lc_style = idx + 26  # 27..36 ('q'..'p')
            uc_style = idx       # 1..10  ('Q'..'P')
            main_k_style = lc_style if is_en26 else uc_style
            if k_sec in sec_map:
                _set_clean_key(
                    sec_map[k_sec],
                    center=ch,
                    up=up_v,
                    down=dn_v,
                    hold=dn_v,
                    fore_style=f"{main_k_style},{up_st},{dn_st}",
                    pos_type="1,2,3",
                    stat_style="__KEEP__",
                )
            if t_sec in sec_map:
                _set_clean_key(
                    sec_map[t_sec],
                    center=ch,
                    up=up_v,
                    down=dn_v,
                    hold=dn_v,
                    fore_style=f"{uc_style},{up_st},{dn_st}",
                    pos_type="1,2,3",
                )

        # Row 2 (KEY11..19 & TIP21..29):
        # A=+, S=home(F42), D=end(F43), F=¥(up=$), G=·(up=%),
        # H=%(up=&), J=《(up=*), K=》(up=(), L=：(up=))
        r2_26_specs = [
            (11, "a", "!", "+", None, None, 110, st_plus),
            (12, "s", "@", "F42", None, None, 111, st_home),
            (13, "d", "#", "F43", None, None, 112, st_end),
            (14, "f", "$", "¥", None, None, st_dollar, st_rmb),
            (15, "g", "%", "·", None, None, 114, st_mid),
            (16, "h", "&", "%", None, None, 115, st_pct),
            (17, "j", "*", "《", None, None, 116, st_lbook),
            (18, "k", "(", "》", None, None, 117, st_rbook),
            (19, "l", ")", "：", None, None, 118, s_colon),
        ]
        for idx, ch, up_v, dn_v, l_v, r_v, up_st, dn_st in r2_26_specs:
            k_sec = f"KEY{idx}"
            t_sec = f"TIP{idx + 10}"
            lc_style = idx + 26  # 37..45 ('a'..'l')
            uc_style = idx       # 11..19 ('A'..'L')
            main_k_style = lc_style if is_en26 else uc_style
            f_k = f"{main_k_style},{up_st},{dn_st}"
            f_uc = f"{uc_style},{up_st},{dn_st}"
            p_tp = "1,2,3"
            h_val = dn_v

            if k_sec in sec_map:
                _set_clean_key(
                    sec_map[k_sec],
                    center=ch,
                    up=up_v,
                    down=dn_v,
                    left=l_v,
                    right=r_v,
                    hold=h_val,
                    fore_style=f_k,
                    pos_type=p_tp,
                    stat_style="__KEEP__",
                )
            if t_sec in sec_map:
                _set_clean_key(
                    sec_map[t_sec],
                    center=ch,
                    up=up_v,
                    down=dn_v,
                    left=l_v,
                    right=r_v,
                    hold=h_val,
                    fore_style=f_uc,
                    pos_type=p_tp,
                )

        # Row 3 (Z..M -> KEY20..26 & TIP30..36): NO DOWN= gestures!
        for idx in range(20, 27):
            k_sec = f"KEY{idx}"
            t_sec = f"TIP{idx + 10}"
            for sec_n in (k_sec, t_sec):
                if sec_n in sec_map:
                    items = sec_map[sec_n]
                    fs = get_sec_val(items, "FORE_STYLE") or ""
                    fs_parts = [x.strip() for x in fs.split(",") if x.strip()][:2]
                    set_sec_val(items, "FORE_STYLE", ",".join(fs_parts))
                    set_sec_val(items, "POS_TYPE", "1,2")
                    remove_sec_key(items, "DOWN")
                    remove_sec_key(items, "LEFT")
                    remove_sec_key(items, "RIGHT")
                    remove_sec_key(items, "HOLD")

        # KEY27 (sym on py_26/def_26; Shift with UP=F1 sym on en_26/en_26s)
        if not is_en26:
            if "KEY27" in sec_map:
                _set_clean_key(
                    sec_map["KEY27"],
                    center="F1",
                    up="F1",
                    down="'",
                    hold="'",
                    fore_style=f"{st_main_sym_lc},{st_sym_sub},{st_sep}",
                    pos_type="1,2,3",
                    stat_style="S4_2",
                )
            if "TIP1" in sec_map:
                _set_clean_key(
                    sec_map["TIP1"],
                    center="F1",
                    up="F1",
                    down="'",
                    fore_style=f"{st_main_sym_lc},{st_sym_sub},{st_sep}",
                    pos_type="1,2,3",
                )
            if "TIP2" in sec_map:
                _set_clean_key(
                    sec_map["TIP2"],
                    center="'",
                    up="F1",
                    down="'",
                    fore_style=f"{st_main_quot},{st_sym_sub},{st_sep}",
                    pos_type="1,2,3",
                )
        else:
            if "KEY27" in sec_map:
                set_sec_val(sec_map["KEY27"], "UP", "F1")
                set_sec_val(sec_map["KEY27"], "FORE_STYLE", f"905,{st_sym_sub}")
                set_sec_val(sec_map["KEY27"], "POS_TYPE", "1,2")
                remove_sec_key(sec_map["KEY27"], "DOWN")
            for t_s, m_st in [("TIP1", "906"), ("TIP2", "907")]:
                if t_s in sec_map:
                    set_sec_val(sec_map[t_s], "UP", "F1")
                    set_sec_val(sec_map[t_s], "FORE_STYLE", f"{m_st},{st_sym_sub}")
                    set_sec_val(sec_map[t_s], "POS_TYPE", "1,2")
                    remove_sec_key(sec_map[t_s], "DOWN")

        # BACK (KEY28): Center=BACK(F36), Up=clear(F48) (NO DOWN=del!)
        if "KEY28" in sec_map:
            _set_clean_key(
                sec_map["KEY28"],
                center="F36",
                up="F48",
                down=None,
                hold="F36",
                fore_style=f"{st_main_back},{st_clear}",
                pos_type="1,2",
            )

        # 123 (KEY29)
        if "KEY29" in sec_map:
            _set_clean_key(
                sec_map["KEY29"],
                center="F6",
                up="F7",
                hold="F7",
                fore_style=f"{st_main_123},{st_emoji}",
                pos_type="1,2",
            )

        # Language key (KEY30):
        #   - Tap (CENTER): Strictly toggles CH <-> EN (F16 on Chinese layouts; F15 on English layouts)
        #   - No UP/DOWN/HOLD
        if "KEY30" in sec_map:
            if not is_en26:
                _set_clean_key(
                    sec_map["KEY30"],
                    center="F16",
                    up=None,
                    down=None,
                    hold=None,
                    fore_style=f"{st_main_en}",
                    pos_type="1",
                )
            else:
                _set_clean_key(
                    sec_map["KEY30"],
                    center="F15",
                    up=None,
                    down=None,
                    hold=None,
                    fore_style=f"{st_main_ch}",
                    pos_type="1",
                )

        # Comma & Period (KEY31, KEY33): UP=home(F42) and UP=end(F43)
        if "KEY31" in sec_map:
            c_ch = "," if is_en26 else "，"
            c_st = st_main_comma_en if is_en26 else st_main_comma_cn
            _set_clean_key(
                sec_map["KEY31"],
                center=c_ch,
                up="F42",
                hold="F42",
                fore_style=f"{c_st},{st_home}",
                pos_type="1,2",
                stat_style="S3_3" if is_en26 else None,
            )
        if is_en26 and "TIP3" in sec_map:
            _set_clean_key(
                sec_map["TIP3"],
                center=",",
                up="F42",
                hold="F42",
                fore_style=f"{st_main_comma_en},{st_home}",
                pos_type="1,2",
            )
        if "KEY33" in sec_map:
            p_ch = "." if is_en26 else "。"
            p_st = st_main_period_en if is_en26 else st_main_period_cn
            _set_clean_key(
                sec_map["KEY33"],
                center=p_ch,
                up="F43",
                hold="F43",
                fore_style=f"{p_st},{st_end}",
                pos_type="1,2",
            )

        # SPACE (KEY32): Center=SPACE(F38), Left=←(F51), Right=→(F52) (removed unsupported 常用语 F77)
        if "KEY32" in sec_map:
            _set_clean_key(
                sec_map["KEY32"],
                center="F38",
                up=None,
                left="F51",
                right="F52",
                fore_style=f"{st_main_space},{st_lt},{st_rt}",
                pos_type="1,150,151",
            )

        # ENTER (KEY34): Center=ENTER(F39), Up=换行(\u2028 soft newline without sending)
        if "KEY34" in sec_map:
            _set_clean_key(
                sec_map["KEY34"],
                center="F39",
                up="\u2028",
                hold="\u2028",
                fore_style=f"{st_main_enter},{st_nl_w}",
                pos_type="1,2",
            )

    # =========================================================================
    # 3. English 9-Key, Stroke & Handwriting Layouts (en_9.ini, en_9s.ini, bh.ini, hw_full.ini, hw_grid.ini)
    # =========================================================================
    if fl in {"en_9.ini", "en_9s.ini", "bh.ini", "hw_full.ini", "hw_grid.ini"}:
        if "KEY1" in sec_map:
            set_sec_val(sec_map["KEY1"], "TOUCH_RECT", "0,0,0,0")
        if fl in {"en_9.ini", "en_9s.ini", "bh.ini"}:
            en9_dn_fixes = [
                ("KEY2", "@", st_at),
                ("KEY3", "—", st_dash),
                ("KEY4", "…", st_ellip),
                ("KEY5", "F42", st_home),
                ("KEY6", "F41", st_tab),
                ("KEY7", "F43", st_end),
            ]
            for k_sec, dn_code, dn_st in en9_dn_fixes:
                if k_sec in sec_map:
                    items = sec_map[k_sec]
                    fs = [x.strip() for x in (get_sec_val(items, "FORE_STYLE") or "").split(",") if x.strip()]
                    if len(fs) >= 2:
                        fs[-1] = str(dn_st)
                    set_sec_val(items, "FORE_STYLE", ",".join(fs))
                    set_sec_val(items, "DOWN", dn_code)
                    set_sec_val(items, "HOLD", dn_code)
        if "KEY11" in sec_map:
            _set_clean_key(
                sec_map["KEY11"],
                center="F36",
                up="F48",
                down=None,
                hold="F36",
                fore_style=f"{st_main_back},{st_clear}",
                pos_type="1,2",
            )
        if "KEY12" in sec_map and fl in {"en_9.ini", "en_9s.ini", "hw_full.ini", "hw_grid.ini"}:
            _set_clean_key(
                sec_map["KEY12"],
                center="F1",
                up="F1",
                down="F7",
                hold="F7",
                fore_style=f"{st_main_sym_uc},{st_sym_sub},{st_emoji}",
                pos_type="1,2,3",
                stat_style="__KEEP__",
            )
        elif "KEY12" in sec_map and fl == "bh.ini":
            _set_clean_key(
                sec_map["KEY12"],
                center="B6",
                up="F1",
                down="F7",
                hold="F7",
                fore_style=f"81,{st_sym_sub},{st_emoji}",
                pos_type="1,2,3",
            )
        if "TIP1" in sec_map and fl in {"en_9.ini", "en_9s.ini", "bh.ini", "hw_full.ini", "hw_grid.ini"}:
            _set_clean_key(
                sec_map["TIP1"],
                center="F48",
                up="F1",
                down="F7",
                fore_style=f"904,{st_sym_sub},{st_emoji}",
                pos_type="1,2,3",
            )
        if "KEY14" in sec_map:
            _set_clean_key(
                sec_map["KEY14"],
                center="F6",
                up="F7",
                hold="F7",
                fore_style=f"{st_main_123},{st_emoji}",
                pos_type="1,2",
            )
        if "KEY15" in sec_map:
            if fl in {"bh.ini", "hw_full.ini", "hw_grid.ini"}:
                _set_clean_key(
                    sec_map["KEY15"],
                    center="F16",
                    up=None,
                    down=None,
                    hold=None,
                    fore_style=f"{st_main_en}",
                    pos_type="1",
                )
            else:
                _set_clean_key(
                    sec_map["KEY15"],
                    center="F15",
                    up=None,
                    down=None,
                    hold=None,
                    fore_style=f"{st_main_ch}",
                    pos_type="1",
                )
        if "KEY16" in sec_map:
            st_zero = reg.get("0", "sub")
            _set_clean_key(
                sec_map["KEY16"],
                center="F38",
                up="0",
                left="F51",
                right="F52",
                fore_style=f"{st_main_space},{st_zero},{st_lt},{st_rt}",
                pos_type="1,2,148,149",
            )
        if "KEY18" in sec_map:
            _set_clean_key(
                sec_map["KEY18"],
                center="F39",
                up="\u2028",
                hold="\u2028",
                fore_style=f"{st_main_enter},{st_nl_w}",
                pos_type="1,2",
            )

    if fl == "voice.ini":
        if "KEY14" in sec_map:
            st_rtn_main = reg.get("返回", "main_md")
            _set_clean_key(
                sec_map["KEY14"],
                center="F4",
                up="F7",
                hold="F7",
                fore_style=f"{st_rtn_main},{st_emoji}",
                pos_type="1,2",
            )
        if "KEY15" in sec_map:
            _set_clean_key(
                sec_map["KEY15"],
                center="F16",
                up=None,
                down=None,
                hold=None,
                fore_style=f"{st_main_en}",
                pos_type="1",
            )
        if "KEY16" in sec_map:
            remove_sec_key(sec_map["KEY16"], "HOLD")
        if "TIP1" in sec_map:
            remove_sec_key(sec_map["TIP1"], "HOLD")
        if "KEY17" in sec_map:
            _set_clean_key(
                sec_map["KEY17"],
                center="F36",
                up="F48",
                down=None,
                hold="F36",
                fore_style=f"{st_main_back},{st_clear}",
                pos_type="1,2",
            )
        if "KEY18" in sec_map:
            _set_clean_key(
                sec_map["KEY18"],
                center="F39",
                up="\u2028",
                hold="\u2028",
                fore_style=f"{st_main_enter},{st_nl_w}",
                pos_type="1,2",
            )

    # =========================================================================
    # 4. Number Pad Layouts (num_9.ini, num_26.ini) — 100% Label-to-Function Matched
    # =========================================================================
    if fl in {"num_9.ini", "num_26.ini"}:
        if "PANEL" in sec_map:
            set_sec_val(sec_map["PANEL"], "NO_BLUR", "1")
        if "LIST" in sec_map:
            set_sec_val(sec_map["LIST"], "LIST_ORDER", "0")
            set_sec_val(sec_map["LIST"], "NAMES", "+ － × ÷ = % ￥ $ ： ； 、 @")
            set_sec_val(sec_map["LIST"], "VALUES", "+ － × ÷ = % ￥ $ ： ； 、 @")
        if "KEY1" in sec_map:
            set_sec_val(sec_map["KEY1"], "TOUCH_RECT", "0,0,0,0")

        # 3x3 Number Grid (KEY2..KEY10):
        #   Center = 1..9 (STYLE62..70)
        #   Up     = ①..⑨
        #   Left   = 壹..玖
        #   Down   = ~, …, #, home(F42), tab(F41), end(F43), :, -, /
        num_grid_specs = [
            ("KEY2", "1", 62, "①", "~", st_tilde, "壹"),
            ("KEY3", "2", 63, "②", "…", st_ellip, "贰"),
            ("KEY4", "3", 64, "③", "#", st_hash, "叁"),
            ("KEY5", "4", 65, "④", "F42", st_home, "肆"),
            ("KEY6", "5", 66, "⑤", "F41", st_tab, "伍"),
            ("KEY7", "6", 67, "⑥", "F43", st_end, "陆"),
            ("KEY8", "7", 68, "⑦", ":", reg.get(":", "sub"), "柒"),
            ("KEY9", "8", 69, "⑧", "-", reg.get("-", "sub"), "捌"),
            ("KEY10", "9", 70, "⑨", "/", reg.get("/", "sub"), "玖"),
        ]
        for k_sec, digit, main_st, up_ch, dn_act, dn_st, left_ch in num_grid_specs:
            if k_sec in sec_map:
                up_st = reg.get(up_ch, "sub")
                left_st = reg.get(left_ch, "sub")
                _set_clean_key(
                    sec_map[k_sec],
                    center=digit,
                    up=up_ch,
                    down=dn_act,
                    left=left_ch,
                    right=None,
                    hold=dn_act,
                    show=None,
                    fore_style=f"{main_st},{up_st},{dn_st},{left_st}",
                    pos_type="1,2,3,148",
                )

        # KEY11: BACK (Center=BACK F36, Up=clear F48, NO DOWN=del!)
        if "KEY11" in sec_map:
            _set_clean_key(
                sec_map["KEY11"],
                center="F36",
                up="F48",
                down=None,
                hold="F36",
                fore_style=f"{st_main_back},{st_clear}",
                pos_type="1,2",
            )

        # KEY12: SYM (Center=SYM F1, Up=SYM F1, Down=表情 F7)
        if "KEY12" in sec_map:
            _set_clean_key(
                sec_map["KEY12"],
                center="F1",
                up="F1",
                down="F7",
                hold="F7",
                fore_style=f"{st_main_sym_uc},{st_sym_sub},{st_emoji}",
                pos_type="1,2,3",
            )

        # KEY13: = / % / ¥ (replaces useless HELP key!)
        if "KEY13" in sec_map:
            st_eq_main = reg.get("=", "main_lg")
            st_pct_sub = reg.get("%", "sub")
            st_rmb_sub = reg.get("¥", "sub")
            _set_clean_key(
                sec_map["KEY13"],
                center="=",
                up="%",
                down="¥",
                hold="¥",
                fore_style=f"{st_eq_main},{st_pct_sub},{st_rmb_sub}",
                pos_type="1,2,3",
            )

        # KEY14: 返回 (Center=返回 F4, Up=表情 F7)
        if "KEY14" in sec_map:
            st_rtn_main = reg.get("返回", "main_md")
            _set_clean_key(
                sec_map["KEY14"],
                center="F4",
                up="F7",
                hold="F7",
                fore_style=f"{st_rtn_main},{st_emoji}",
                pos_type="1,2",
            )

        # KEY15: SPACE (Center=SPACE F38, Left=← F51, Right=→ F52; removed unsupported 常用语 F77)
        if "KEY15" in sec_map:
            _set_clean_key(
                sec_map["KEY15"],
                center="F38",
                up=None,
                left="F51",
                right="F52",
                hold=None,
                fore_style=f"{st_main_space},{st_lt},{st_rt}",
                pos_type="1,148,149",
            )

        # KEY16: 0 (Center=0, Up=⑩, Left=零, Down=00)
        if "KEY16" in sec_map:
            st_ten = reg.get("⑩", "sub")
            st_ling = reg.get("零", "sub")
            st_00 = reg.get("00", "sub")
            _set_clean_key(
                sec_map["KEY16"],
                center="0",
                up="⑩",
                down="00",
                left="零",
                hold="00",
                fore_style=f"71,{st_ten},{st_00},{st_ling}",
                pos_type="1,2,3,148",
            )

        # KEY17: . (Center=., Up=，, Down=。)
        if "KEY17" in sec_map:
            st_c_sub = reg.get("，", "sub")
            st_p_sub = reg.get("。", "sub")
            _set_clean_key(
                sec_map["KEY17"],
                center=".",
                up="，",
                down="。",
                hold="。",
                fore_style=f"72,{st_c_sub},{st_p_sub}",
                pos_type="1,2,3",
            )

        # KEY18: ENTER (Center=ENTER F39, Up=换行 \u2028)
        if "KEY18" in sec_map:
            _set_clean_key(
                sec_map["KEY18"],
                center="F39",
                up="\u2028",
                hold="\u2028",
                fore_style=f"{st_main_enter},{st_nl_w}",
                pos_type="1,2",
            )

    # =========================================================================
    # 5. Candidate expansion & Symbol panels (sel_ch.ini, sel_en.ini, symbol.ini)
    # =========================================================================
    if fl in {"sel_ch.ini", "sel_en.ini", "symbol.ini"}:
        if "CAND" in sec_map:
            sec_map["CAND"].clear()
        if "MORE" in sec_map:
            sec_map["MORE"].clear()
        if "PANEL" in sec_map:
            set_sec_val(sec_map["PANEL"], "NO_BLUR", "0")
            set_sec_val(sec_map["PANEL"], "SIZE", "1125,650" if orientation == "port" else "1920,860")
        if "LIST" in sec_map:
            if orientation == "port":
                set_sec_val(sec_map["LIST"], "CELL_SIZE", "171,97")
                set_sec_val(sec_map["LIST"], "POS", "9,17")
            else:
                set_sec_val(sec_map["LIST"], "CELL_SIZE", "291,129")
                set_sec_val(sec_map["LIST"], "POS", "15,23")
            set_sec_val(sec_map["LIST"], "LIST_NUM", "5")
            set_sec_val(sec_map["LIST"], "LIST_ORDER", "0")
            remove_sec_key(sec_map["LIST"], "NAMES")
            remove_sec_key(sec_map["LIST"], "VALUES")

        if "KEY11" in sec_map:
            remove_sec_key(sec_map["KEY11"], "LEFT")
            set_sec_val(sec_map["KEY11"], "CENTER", "F4")
            set_sec_val(sec_map["KEY11"], "UP", "F4")
            set_sec_val(sec_map["KEY11"], "DOWN", "F4")
        if "KEY15" in sec_map:
            remove_sec_key(sec_map["KEY15"], "LEFT")
            set_sec_val(sec_map["KEY15"], "CENTER", "F36")
            set_sec_val(sec_map["KEY15"], "HOLD", "F36")
            if fl != "symbol.ini":
                set_sec_val(sec_map["KEY15"], "DOWN", "F36")

        if orientation == "port":
            if "KEY1" in sec_map:
                set_sec_val(sec_map["KEY1"], "VIEW_RECT", "4,12,179,498")
                remove_sec_key(sec_map["KEY1"], "TOUCH_RECT")
            if "KEY2" in sec_map:
                set_sec_val(sec_map["KEY2"], "VIEW_RECT", "183,12,938,498")
                remove_sec_key(sec_map["KEY2"], "TOUCH_RECT")
            if "KEY3" in sec_map:
                set_sec_val(sec_map["KEY3"], "VIEW_RECT", "187,16,930,484")
            if "KEY11" in sec_map:
                set_sec_val(sec_map["KEY11"], "VIEW_RECT", "4,510,179,132")
            if "KEY12" in sec_map:
                set_sec_val(sec_map["KEY12"], "VIEW_RECT", "183,510,253,132")
            if "KEY13" in sec_map:
                set_sec_val(sec_map["KEY13"], "VIEW_RECT", "436,510,253,132")
            if "KEY14" in sec_map:
                set_sec_val(sec_map["KEY14"], "VIEW_RECT", "689,510,253,132")
            if "KEY15" in sec_map:
                set_sec_val(sec_map["KEY15"], "VIEW_RECT", "942,510,179,132")
        else:
            if "KEY1" in sec_map:
                set_sec_val(sec_map["KEY1"], "VIEW_RECT", "6,16,305,655")
                remove_sec_key(sec_map["KEY1"], "TOUCH_RECT")
            if "KEY2" in sec_map:
                set_sec_val(sec_map["KEY2"], "VIEW_RECT", "312,16,1600,655")
                remove_sec_key(sec_map["KEY2"], "TOUCH_RECT")
            if "KEY3" in sec_map:
                set_sec_val(sec_map["KEY3"], "VIEW_RECT", "319,21,1587,640")
            if "KEY11" in sec_map:
                set_sec_val(sec_map["KEY11"], "VIEW_RECT", "6,671,305,177")
            if "KEY12" in sec_map:
                set_sec_val(sec_map["KEY12"], "VIEW_RECT", "312,671,431,177")
            if "KEY13" in sec_map:
                set_sec_val(sec_map["KEY13"], "VIEW_RECT", "744,671,431,177")
            if "KEY14" in sec_map:
                set_sec_val(sec_map["KEY14"], "VIEW_RECT", "1175,671,431,177")
            if "KEY15" in sec_map:
                set_sec_val(sec_map["KEY15"], "VIEW_RECT", "1607,671,305,177")

    # 6. Fix help.ini full-screen panel height
    if fl == "help.ini":
        if "CAND" in sec_map:
            sec_map["CAND"].clear()
        target_size = "1125,650" if orientation == "port" else "1920,860"
        if "PANEL" in sec_map:
            set_sec_val(sec_map["PANEL"], "SIZE", target_size)
        if "KEY1" in sec_map:
            set_sec_val(sec_map["KEY1"], "VIEW_RECT", f"0,0,{target_size}")

    return _normalize_key_tip_sections(orientation, fl, sections)


def _normalize_key_tip_sections(orientation: str, fl: str, sections: list):
    """Strip Android KEY_TOP_* sections while preserving original KEY1..N and TIP1..M section numbers and STAT_STYLE links."""
    sections = [
        [sec_name, items]
        for sec_name, items in sections
        if not (sec_name and re.match(r"^KEY_TOP_\d+$", sec_name, re.I))
    ]

    layout_targets = {
        "bh.ini",
        "def_26.ini",
        "def_9.ini",
        "en_26.ini",
        "en_26s.ini",
        "en_9.ini",
        "en_9s.ini",
        "hw_full.ini",
        "hw_grid.ini",
        "num_26.ini",
        "num_9.ini",
        "py_14.ini",
        "py_26.ini",
        "py_9.ini",
        "voice.ini",
    }
    if fl in layout_targets:
        is_port = orientation == "port"
        cand_rect = "0,0,1125,114" if is_port else "0,0,1920,139"
        panel_size = "1125,536" if is_port else "1920,720"
        for sec_name, items in sections:
            if sec_name == "CAND":
                set_sec_val(items, "LAYOUT_NAME", "cand1")
                set_sec_val(items, "VIEW_RECT", cand_rect)
                set_sec_val(items, "TYPE", "4")
            elif sec_name == "PANEL":
                if get_sec_val(items, "SIZE") is None:
                    set_sec_val(items, "SIZE", panel_size)
        if not any(s[0] == "SCAND" for s in sections):
            sections.append(["SCAND", []])

    return sections


def build_japanese_9key_ini(orientation: str, mode: str, reg: StyleRegistry):
    """
    Build Japanese 9-Key layouts (jp_9.ini, jp_daku.ini, jp_kana.ini) using the exact 9-key geometry
    of num_9.ini (20 keys total: 3 left punctuation keys + 3x3 grid + 3 right keys + 5 bottom keys).
      mode == 'hira' : Hiragana Gojūon (あ〜わ + っ/ゃ/ゅ/ぇ/ょ)
      mode == 'daku' : Dakuon / Handakuon (が・ざ・だ・ば・ぱ・ヴ・ガ・ザ・ダ・バ)
      mode == 'kana' : Katakana Gojūon (ア〜ワ + ッ/ャ/ュ/ェ/ョ)
    """
    is_port = orientation == "port"
    left_x = 4 if is_port else 6
    left_w = 179 if is_port else 305

    grid_xs = [183, 436, 689] if is_port else [312, 744, 1175]
    grid_ys = [0, 132, 264] if is_port else [0, 177, 354]
    grid_w, grid_h = (253, 132) if is_port else (431, 177)

    right_x = 942 if is_port else 1607
    right_w = 179 if is_port else 305
    bot_y = 396 if is_port else 532

    if mode == "hira":
        grid_cells = [
            ("あ", "う", "お", "い", "え"),
            ("か", "く", "こ", "き", "け"),
            ("さ", "す", "そ", "し", "せ"),
            ("た", "つ", "と", "ち", "て"),
            ("な", "ぬ", "の", "に", "ね"),
            ("は", "ふ", "ほ", "ひ", "へ"),
            ("ま", "む", "も", "み", "め"),
            ("や", "ゆ", "よ", "「", "」"),
            ("ら", "る", "ろ", "り", "れ"),
        ]
        bot_center = ("わ", "ん", "～", "を", "ー")
        small_cell = ("っ", "ゅ", "ょ", "ゃ", "ぇ")
        k12_spec = ("濁音゛", "Z+jp_daku", "ア片", "Z+jp_kana", "・", "・")
    elif mode == "daku":
        grid_cells = [
            ("が", "ぐ", "ご", "ぎ", "げ"),
            ("ざ", "ず", "ぞ", "じ", "ぜ"),
            ("だ", "づ", "ど", "ぢ", "で"),
            ("ば", "ぶ", "ぼ", "び", "べ"),
            ("ぱ", "ぷ", "ぽ", "ぴ", "ぺ"),
            ("ヴ", "ぃ", "ぇ", "ぁ", "ぅ"),
            ("ガ", "グ", "ゴ", "ギ", "ゲ"),
            ("ザ", "ズ", "ゾ", "ジ", "ゼ"),
            ("ダ", "ヅ", "ド", "ヂ", "デ"),
        ]
        bot_center = ("バ", "ブ", "ボ", "ビ", "ベ")
        small_cell = ("っ", "ゅ", "ょ", "ゃ", "ぇ")
        k12_spec = ("あ平", "Z+jp_9", "ア片", "Z+jp_kana", "・", "・")
    else:
        grid_cells = [
            ("ア", "ウ", "オ", "イ", "エ"),
            ("カ", "ク", "コ", "キ", "ケ"),
            ("サ", "ス", "ソ", "シ", "セ"),
            ("タ", "ツ", "ト", "チ", "テ"),
            ("ナ", "ヌ", "ノ", "ニ", "ネ"),
            ("ハ", "フ", "ホ", "ヒ", "ヘ"),
            ("マ", "ム", "モ", "ミ", "メ"),
            ("ヤ", "ユ", "ヨ", "「", "」"),
            ("ラ", "ル", "ロ", "リ", "レ"),
        ]
        bot_center = ("ワ", "ン", "～", "ヲ", "ー")
        small_cell = ("ッ", "ュ", "ョ", "ャ", "ェ")
        k12_spec = ("あ平", "Z+jp_9", "濁音゛", "Z+jp_daku", "・", "・")

    cand_rect = "0,0,1125,114" if is_port else "0,0,1920,139"
    panel_size = "1125,536" if is_port else "1920,720"
    sections = [
        ["INPUT", []],
        ["CAND", [["LAYOUT_NAME", "cand1"], ["VIEW_RECT", cand_rect], ["TYPE", "4"]]],
        ["PANEL", [["NO_BLUR", "1"], ["SIZE", panel_size], ["KEY_NUM", "20"], ["TIP_NUM", "0"]]],
        ["MORE", []],
        ["HINT", [["LAYOUT_NAME", "hint1"], ["TYPE", "0"]]],
    ]

    # KEY1..KEY3: Left column 3 Japanese punctuation keys (replaces [LIST] so ListView never overlaps JP/RU)
    left_punct_specs = [
        ("、", "「", "」"),
        ("。", "！", "？"),
        ("・", "～", "ー"),
    ]
    for idx, (lp_c, lp_u, lp_d) in enumerate(left_punct_specs):
        st_c = reg.get(lp_c, "main_lg")
        st_u = reg.get(lp_u, "sub")
        st_d = reg.get(lp_d, "sub")
        sections.append([
            f"KEY{idx + 1}",
            [
                ["BACK_STYLE", "233"],
                ["VIEW_RECT", f"{left_x},{grid_ys[idx]},{left_w},{grid_h}"],
                ["CENTER", lp_c],
                ["UP", lp_u],
                ["DOWN", lp_d],
                ["HOLD", lp_d],
                ["FORE_STYLE", f"{st_c},{st_u},{st_d}"],
                ["POS_TYPE", "1,2,3"],
                ["BACK_ANIM_STYLE", "503"],
                ["FORE_ANIM_STYLE", "503"],
            ],
        ])

    # KEY4..KEY12 (3x3 grid)
    for idx, (c, u, d, l, r) in enumerate(grid_cells):
        kx = grid_xs[idx % 3]
        ky = grid_ys[idx // 3]
        st_c = reg.get(c, "main_lg")
        st_u = reg.get(u, "sub")
        st_d = reg.get(d, "sub")
        st_l = reg.get(l, "sub")
        st_r = reg.get(r, "sub")
        sections.append([
            f"KEY{idx + 4}",
            [
                ["BACK_STYLE", "232"],
                ["VIEW_RECT", f"{kx},{ky},{grid_w},{grid_h}"],
                ["CENTER", c],
                ["UP", u],
                ["DOWN", d],
                ["LEFT", l],
                ["RIGHT", r],
                ["HOLD", d],
                ["FORE_STYLE", f"{st_c},{st_u},{st_d},{st_l},{st_r}"],
                ["POS_TYPE", "1,2,3,148,149"],
                ["BACK_ANIM_STYLE", "502"],
                ["FORE_ANIM_STYLE", "502"],
            ],
        ])

    # KEY13: BACK (Center=BACK F36, Up=clear F48, NO DOWN=del!)
    st_back = reg.get("BACK", "main_sm")
    st_clear = reg.get("clear", "sub")
    sections.append([
        "KEY13",
        [
            ["BACK_STYLE", "233"],
            ["VIEW_RECT", f"{right_x},{grid_ys[0]},{right_w},{grid_h}"],
            ["CENTER", "F36"],
            ["UP", "F48"],
            ["HOLD", "F36"],
            ["FORE_STYLE", f"{st_back},{st_clear}"],
            ["POS_TYPE", "1,2"],
            ["BACK_ANIM_STYLE", "503"],
            ["FORE_ANIM_STYLE", "503"],
        ],
    ])

    # KEY14: 濁音゛ / あ平 / ア片 / ・
    k12_c_lbl, k12_c_act, k12_u_lbl, k12_u_act, k12_d_lbl, k12_d_act = k12_spec
    st_k12_c = reg.get(k12_c_lbl, "main_md")
    st_k12_u = reg.get(k12_u_lbl, "sub")
    st_k12_d = reg.get(k12_d_lbl, "sub")
    sections.append([
        "KEY14",
        [
            ["BACK_STYLE", "233"],
            ["VIEW_RECT", f"{right_x},{grid_ys[1]},{right_w},{grid_h}"],
            ["CENTER", k12_c_act],
            ["UP", k12_u_act],
            ["DOWN", k12_d_act],
            ["HOLD", k12_d_act],
            ["FORE_STYLE", f"{st_k12_c},{st_k12_u},{st_k12_d}"],
            ["POS_TYPE", "1,2,3"],
            ["BACK_ANIM_STYLE", "503"],
            ["FORE_ANIM_STYLE", "503"],
        ],
    ])

    # KEY15: Small kana 5-way flick (っ/ゃ/ゅ/ぇ/ょ)
    sc_c, sc_u, sc_d, sc_l, sc_r = small_cell
    st_sc_c = reg.get(sc_c, "main_lg")
    st_sc_u = reg.get(sc_u, "sub")
    st_sc_d = reg.get(sc_d, "sub")
    st_sc_l = reg.get(sc_l, "sub")
    st_sc_r = reg.get(sc_r, "sub")
    sections.append([
        "KEY15",
        [
            ["BACK_STYLE", "233"],
            ["VIEW_RECT", f"{right_x},{grid_ys[2]},{right_w},{grid_h}"],
            ["CENTER", sc_c],
            ["UP", sc_u],
            ["DOWN", sc_d],
            ["LEFT", sc_l],
            ["RIGHT", sc_r],
            ["HOLD", sc_d],
            ["FORE_STYLE", f"{st_sc_c},{st_sc_u},{st_sc_d},{st_sc_l},{st_sc_r}"],
            ["POS_TYPE", "1,2,3,146,147"],
            ["BACK_ANIM_STYLE", "503"],
            ["FORE_ANIM_STYLE", "503"],
        ],
    ])

    # KEY16: 返回 (bottom-left — exits Z+ custom sub-panel cleanly via F4)
    st_rtn = reg.get("返回", "main_md")
    st_emoji = reg.get("表情", "sub")
    sections.append([
        "KEY16",
        [
            ["BACK_STYLE", "233"],
            ["VIEW_RECT", f"{4 if is_port else 6},{bot_y},{right_w},{grid_h}"],
            ["CENTER", "F4"],
            ["UP", "F7"],
            ["HOLD", "F7"],
            ["FORE_STYLE", f"{st_rtn},{st_emoji}"],
            ["POS_TYPE", "1,2"],
            ["BACK_ANIM_STYLE", "503"],
            ["FORE_ANIM_STYLE", "503"],
        ],
    ])

    # KEY17: Language key (Tap CH -> F4 returns to CH/EN; Swipe UP -> Z+ru_jcuken cycles JP<->RU)
    st_ch_m = reg.get("CH", "main_md")
    st_ru_s = reg.get("RU", "sub")
    sections.append([
        "KEY17",
        [
            ["BACK_STYLE", "233"],
            ["VIEW_RECT", f"{grid_xs[0]},{bot_y},{grid_w},{grid_h}"],
            ["CENTER", "F4"],
            ["UP", "Z+ru_jcuken"],
            ["HOLD", "Z+ru_jcuken"],
            ["FORE_STYLE", f"{st_ch_m},{st_ru_s}"],
            ["POS_TYPE", "1,2"],
            ["BACK_ANIM_STYLE", "503"],
            ["FORE_ANIM_STYLE", "503"],
        ],
    ])

    # KEY18: わ row (bottom-center)
    bc_c, bc_u, bc_d, bc_l, bc_r = bot_center
    st_bc_c = reg.get(bc_c, "main_lg")
    st_bc_u = reg.get(bc_u, "sub")
    st_bc_d = reg.get(bc_d, "sub")
    st_bc_l = reg.get(bc_l, "sub")
    st_bc_r = reg.get(bc_r, "sub")
    sections.append([
        "KEY18",
        [
            ["BACK_STYLE", "232"],
            ["VIEW_RECT", f"{grid_xs[1]},{bot_y},{grid_w},{grid_h}"],
            ["CENTER", bc_c],
            ["UP", bc_u],
            ["DOWN", bc_d],
            ["LEFT", bc_l],
            ["RIGHT", bc_r],
            ["HOLD", bc_d],
            ["FORE_STYLE", f"{st_bc_c},{st_bc_u},{st_bc_d},{st_bc_l},{st_bc_r}"],
            ["POS_TYPE", "1,2,3,148,149"],
            ["BACK_ANIM_STYLE", "502"],
            ["FORE_ANIM_STYLE", "502"],
        ],
    ])

    # KEY19: SPACE
    st_sp = reg.get("SPACE", "main_sm")
    st_ph = reg.get("常用语", "sub")
    st_lt = reg.get("←", "sub")
    st_rt = reg.get("→", "sub")
    sections.append([
        "KEY19",
        [
            ["BACK_STYLE", "233"],
            ["VIEW_RECT", f"{grid_xs[2]},{bot_y},{grid_w},{grid_h}"],
            ["CENTER", "F38"],
            ["UP", "F77"],
            ["LEFT", "F51"],
            ["RIGHT", "F52"],
            ["FORE_STYLE", f"{st_sp},{st_ph},{st_lt},{st_rt}"],
            ["POS_TYPE", "1,2,148,149"],
            ["BACK_ANIM_STYLE", "503"],
            ["FORE_ANIM_STYLE", "503"],
        ],
    ])

    # KEY20: ENTER (Center=ENTER F39, Up=换行 \u2028)
    st_ent = reg.get("ENTER", "main_enter")
    st_nl_w = reg.get("换行", "sub_white")
    sections.append([
        "KEY20",
        [
            ["BACK_STYLE", "235"],
            ["VIEW_RECT", f"{right_x},{bot_y},{right_w},{grid_h}"],
            ["CENTER", "F39"],
            ["UP", "\u2028"],
            ["HOLD", "\u2028"],
            ["FORE_STYLE", f"{st_ent},{st_nl_w}"],
            ["POS_TYPE", "1,2"],
            ["BACK_ANIM_STYLE", "503"],
            ["FORE_ANIM_STYLE", "503"],
        ],
    ])

    sections.append(["SCAND", []])
    return sections


def build_cyrillic_ini(orientation: str, is_caps: bool, reg: StyleRegistry):
    """
    Build Russian Cyrillic ЙЦУКЕН 33-letter layouts (ru_jcuken.ini & ru_caps.ini).
    11 + 11 + 11 + 6 = 39 keys total.
    """
    is_port = orientation == "port"
    kh = 132 if is_port else 177
    y1, y2, y3, y4 = (0, 132, 264, 396) if is_port else (0, 177, 354, 532)
    r12_x0, r12_w = (7, 101) if is_port else (6, 173)

    cand_rect = "0,0,1125,114" if is_port else "0,0,1920,139"
    panel_size = "1125,536" if is_port else "1920,720"
    sections = [
        ["INPUT", []],
        ["CAND", [["LAYOUT_NAME", "cand1"], ["VIEW_RECT", cand_rect], ["TYPE", "4"]]],
        ["PANEL", [["NO_BLUR", "1"], ["SIZE", panel_size], ["KEY_NUM", "39"], ["TIP_NUM", "0"]]],
        ["MORE", []],
        ["HINT", [["LAYOUT_NAME", "hint2"]]],
    ]

    r1_specs = [
        ("й", "1", "…", "…"),
        ("ц", "2", "~", "~"),
        ("у", "3", "€", "€"),
        ("к", "4", "F48", "clear"),
        ("е", "5", "Ё" if is_caps else "ё", "Ё" if is_caps else "ё"),
        ("н", "6", "«", "«"),
        ("г", "7", "»", "»"),
        ("ш", "8", "—", "—"),
        ("щ", "9", "№", "№"),
        ("з", "0", ":", ":"),
        ("х", "Ъ" if is_caps else "ъ", ";", ";"),
    ]
    key_idx = 1
    for i, (ch, up_act, dn_act, dn_lbl) in enumerate(r1_specs):
        c_out = ch.upper() if is_caps else ch
        st_c = reg.get(c_out, "main_lg")
        st_u = reg.get(up_act, "sub")
        st_d = reg.get(dn_lbl, "sub")
        kx = r12_x0 + i * r12_w
        sections.append([
            f"KEY{key_idx}",
            [
                ["BACK_STYLE", "230"],
                ["VIEW_RECT", f"{kx},{y1},{r12_w},{kh}"],
                ["CENTER", c_out],
                ["UP", up_act],
                ["DOWN", dn_act],
                ["HOLD", dn_act],
                ["FORE_STYLE", f"{st_c},{st_u},{st_d}"],
                ["POS_TYPE", "1,2,3"],
                ["BACK_ANIM_STYLE", "500"],
                ["FORE_ANIM_STYLE", "500"],
            ],
        ])
        key_idx += 1

    r2_specs = [
        ("ф", "!", "·", "·"),
        ("ы", "@", "F42", "home"),
        ("в", "#", "F43", "end"),
        ("а", "$", "(", "("),
        ("п", "%", ")", ")"),
        ("р", "F49", "F50", "CURSOR"),
        ("о", "&", "-", "-"),
        ("л", "*", "_", "_"),
        ("д", "/", "=", "="),
        ("ж", "?", "+", "+"),
        ("э", "\"", "'", "'"),
    ]
    for i, (ch, up_act, dn_act, dn_lbl) in enumerate(r2_specs):
        c_out = ch.upper() if is_caps else ch
        st_c = reg.get(c_out, "main_lg")
        kx = r12_x0 + i * r12_w
        if dn_lbl == "CURSOR":
            st_u = reg.get("↑", "sub")
            st_d = reg.get("↓", "sub")
            st_l = reg.get("←", "sub")
            st_r = reg.get("→", "sub")
            sections.append([
                f"KEY{key_idx}",
                [
                    ["BACK_STYLE", "230"],
                    ["VIEW_RECT", f"{kx},{y2},{r12_w},{kh}"],
                    ["CENTER", c_out],
                    ["UP", "F49"],
                    ["DOWN", "F50"],
                    ["LEFT", "F51"],
                    ["RIGHT", "F52"],
                    ["FORE_STYLE", f"{st_c},{st_u},{st_d},{st_l},{st_r}"],
                    ["POS_TYPE", "1,2,3,4,5"],
                    ["BACK_ANIM_STYLE", "500"],
                    ["FORE_ANIM_STYLE", "500"],
                ],
            ])
        else:
            st_u = reg.get(up_act, "sub")
            st_d = reg.get(dn_lbl, "sub")
            sections.append([
                f"KEY{key_idx}",
                [
                    ["BACK_STYLE", "230"],
                    ["VIEW_RECT", f"{kx},{y2},{r12_w},{kh}"],
                    ["CENTER", c_out],
                    ["UP", up_act],
                    ["DOWN", dn_act],
                    ["HOLD", dn_act],
                    ["FORE_STYLE", f"{st_c},{st_u},{st_d}"],
                    ["POS_TYPE", "1,2,3"],
                    ["BACK_ANIM_STYLE", "500"],
                    ["FORE_ANIM_STYLE", "500"],
                ],
            ])
        key_idx += 1

    # Row 3: KEY23 (АБВ/абв toggle), KEY24..32 (9 letters я..ю), KEY33 (BACK)
    sh_rect = "4,264,108,132" if is_port else "6,354,185,177"
    sh_lbl = "абв" if is_caps else "АБВ"
    sh_target = "Z+ru_jcuken" if is_caps else "Z+ru_caps"
    st_sh = reg.get(sh_lbl, "main_md")
    st_no = reg.get("№", "sub")
    sections.append([
        f"KEY{key_idx}",
        [
            ["BACK_STYLE", "231"],
            ["VIEW_RECT", sh_rect],
            ["CENTER", sh_target],
            ["UP", "№"],
            ["HOLD", "№"],
            ["FORE_STYLE", f"{st_sh},{st_no}"],
            ["POS_TYPE", "1,2"],
            ["BACK_ANIM_STYLE", "501"],
            ["FORE_ANIM_STYLE", "501"],
        ],
    ])
    key_idx += 1

    r3_letters = ["я", "ч", "с", "м", "и", "т", "ь", "б", "ю"]
    r3_x0, r3_w = (112, 100) if is_port else (191, 171)
    for i, ch in enumerate(r3_letters):
        c_out = ch.upper() if is_caps else ch
        u_out = ch if is_caps else ch.upper()
        st_c = reg.get(c_out, "main_lg")
        st_u = reg.get(u_out, "sub")
        kx = r3_x0 + i * r3_w
        sections.append([
            f"KEY{key_idx}",
            [
                ["BACK_STYLE", "230"],
                ["VIEW_RECT", f"{kx},{y3},{r3_w},{kh}"],
                ["CENTER", c_out],
                ["UP", u_out],
                ["FORE_STYLE", f"{st_c},{st_u}"],
                ["POS_TYPE", "1,2"],
                ["BACK_ANIM_STYLE", "500"],
                ["FORE_ANIM_STYLE", "500"],
            ],
        ])
        key_idx += 1

    # KEY33: BACK (Center=BACK F36, Up=clear F48, NO DOWN=del!)
    bk_rect = "1012,264,109,132" if is_port else "1730,354,183,177"
    st_back = reg.get("BACK", "main_sm")
    st_clear = reg.get("clear", "sub")
    sections.append([
        f"KEY{key_idx}",
        [
            ["BACK_STYLE", "231"],
            ["VIEW_RECT", bk_rect],
            ["CENTER", "F36"],
            ["UP", "F48"],
            ["HOLD", "F36"],
            ["FORE_STYLE", f"{st_back},{st_clear}"],
            ["POS_TYPE", "1,2"],
            ["BACK_ANIM_STYLE", "501"],
            ["FORE_ANIM_STYLE", "501"],
        ],
    ])
    key_idx += 1

    # Row 4 (KEY34..39): 返回, CH(UP=JP), ',', SPACE, '.', ENTER
    r4_rects = (
        ["4,396,132,132", "136,396,112,132", "248,396,112,132", "360,396,448,132", "808,396,112,132", "920,396,201,132"]
        if is_port
        else ["6,532,225,177", "232,532,191,177", "423,532,191,177", "614,532,764,177", "1378,532,191,177", "1570,532,343,177"]
    )
    st_rtn = reg.get("返回", "main_md")
    st_emoji = reg.get("表情", "sub")
    st_ch_m = reg.get("CH", "main_md")
    st_jp_s = reg.get("JP", "sub")
    st_cm = reg.get(",", "main_lg")
    st_hm = reg.get("home", "sub")
    st_sp = reg.get("SPACE", "main_sm")
    st_ph = reg.get("常用语", "sub")
    st_lt = reg.get("←", "sub")
    st_rt = reg.get("→", "sub")
    st_pr = reg.get(".", "main_lg")
    st_ed = reg.get("end", "sub")
    st_ent = reg.get("ENTER", "main_enter")
    st_nl_w = reg.get("换行", "sub_white")

    r4_defs = [
        ("231", r4_rects[0], [["CENTER", "F4"], ["UP", "F7"], ["HOLD", "F7"], ["FORE_STYLE", f"{st_rtn},{st_emoji}"], ["POS_TYPE", "1,2"]]),
        ("231", r4_rects[1], [["CENTER", "F4"], ["UP", "Z+jp_9"], ["HOLD", "Z+jp_9"], ["FORE_STYLE", f"{st_ch_m},{st_jp_s}"], ["POS_TYPE", "1,2"]]),
        ("230", r4_rects[2], [["CENTER", ","], ["UP", "F42"], ["HOLD", "F42"], ["FORE_STYLE", f"{st_cm},{st_hm}"], ["POS_TYPE", "1,2"]]),
        ("230", r4_rects[3], [["CENTER", "F38"], ["UP", "F77"], ["LEFT", "F51"], ["RIGHT", "F52"], ["FORE_STYLE", f"{st_sp},{st_ph},{st_lt},{st_rt}"], ["POS_TYPE", "1,2,150,151"]]),
        ("231", r4_rects[4], [["CENTER", "."], ["UP", "F43"], ["HOLD", "F43"], ["FORE_STYLE", f"{st_pr},{st_ed}"], ["POS_TYPE", "1,2"]]),
        ("234", r4_rects[5], [["CENTER", "F39"], ["UP", "\u2028"], ["HOLD", "\u2028"], ["FORE_STYLE", f"{st_ent},{st_nl_w}"], ["POS_TYPE", "1,2"]]),
    ]
    for bs, vr, kv_list in r4_defs:
        sections.append([
            f"KEY{key_idx}",
            [["BACK_STYLE", bs], ["VIEW_RECT", vr]] + kv_list + [["BACK_ANIM_STYLE", "501"], ["FORE_ANIM_STYLE", "501"]],
        ])
        key_idx += 1

    sections.append(["SCAND", []])
    return sections


def build_exact_keycaps_and_remap(theme_dir: Path, parsed_inis: dict):
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


def patch_default_css(text: str, is_dark: bool, extra_anjian_css: str, reg: StyleRegistry) -> str:
    text = re.sub(r"PRESS_SOUND_PATH\s*=\s*aj\.ogg", "PRESS_SOUND_PATH=aj.aiff", text, flags=re.IGNORECASE)
    text = re.sub(r"PRESS_SOUND_PATH\s*=\s*b\.ogg", "PRESS_SOUND_PATH=b.aiff", text, flags=re.IGNORECASE)
    text = re.sub(r"STYLE_NUM\s*=\s*\d+", "STYLE_NUM=1500", text)

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
            if su in seen_sec_names:
                continue
            seen_sec_names.add(su)
        deduped_sections.append([sec_name, cleaned_items])
    text = serialize_ini_sections(deduped_sections)

    nm_sub = "8c8c8c" if is_dark else "000000"
    hl_sub = "ebebeb" if is_dark else "ffffff"

    gesture_styles = []
    for st_id, label, custom_nm in [
        (144, "undo", nm_sub),
        (145, "esc", nm_sub),
        (146, "clear", nm_sub),
        (147, "up", nm_sub),
        (148, "down", nm_sub),
        (149, "del", nm_sub),
    ]:
        if f"[STYLE{st_id}]" not in text:
            gesture_styles.append(
                f"[STYLE{st_id}]\r\nSHOW={label}\r\nNM_COLOR={custom_nm}\r\nHL_COLOR={hl_sub}\r\n"
                f"FONT_SIZE=20\r\nBORDER_COLOR=AFFFFFFF\r\nBORDER_SIZE=1"
            )

    if is_dark:
        logo_styles = (
            "[STYLE247]\r\nNM_COLOR=222222\r\nHL_COLOR=222222\r\n\r\n"
            "[STYLE248]\r\nNM_COLOR=2c2c2e\r\nHL_COLOR=3a3a3c\r\n\r\n"
            "[STYLE249]\r\nFONT_SIZE=36\r\nNM_COLOR=ebebeb\r\nHL_COLOR=ed5858"
        )
        scand_color = "ff222222"
    else:
        logo_styles = (
            "[STYLE247]\r\nNM_COLOR=ededed\r\nHL_COLOR=ededed\r\n\r\n"
            "[STYLE248]\r\nNM_COLOR=ffffff\r\nHL_COLOR=e5e5e5\r\n\r\n"
            "[STYLE249]\r\nFONT_SIZE=36\r\nNM_COLOR=333333\r\nHL_COLOR=f75757"
        )
        scand_color = "ffededed"

    appended = [text.rstrip()]
    if gesture_styles:
        appended.append("\r\n\r\n".join(gesture_styles))
    if "[STYLE247]" not in text:
        appended.append(logo_styles)
    reg_css = reg.emit_css_blocks(is_dark=is_dark)
    if reg_css:
        appended.append(reg_css)
    if extra_anjian_css:
        appended.append(extra_anjian_css)
    if "[STYLE922]" not in text:
        appended.append(f"[STYLE922]\r\nNM_COLOR={scand_color}")

    return "\r\n\r\n".join(appended) + "\r\n"


def build_ios_logo_assets(theme_dir: Path):
    cand_nm = Image.open(theme_dir / "res" / "cand.png").convert("RGBA").crop((0, 0, 136, 110))
    cand2_path = theme_dir / "res" / "cand2.png"
    cand_hl = (
        Image.open(cand2_path).convert("RGBA").crop((0, 0, 136, 110))
        if cand2_path.exists()
        else cand_nm.copy()
    )
    buf_nm = io.BytesIO()
    cand_nm.save(buf_nm, format="PNG", optimize=True)
    buf_hl = io.BytesIO()
    cand_hl.save(buf_hl, format="PNG", optimize=True)

    plist_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        "<plist version=\"1.0\"><array>"
        "<dict><key>Name</key><string>0</string><key>Images</key><dict>"
        "<key>Normal</key><dict><key>Normal</key><string>logo_Normal</string><key>Highlight</key><string>logo_Highlight</string><key>Selected</key><string>logo_Normal</string></dict>"
        "<key>Theme</key><dict><key>Normal</key><string>logo_Normal</string><key>Highlight</key><string>logo_Highlight</string><key>Selected</key><string>logo_Normal</string></dict>"
        "</dict></dict>"
        "</array></plist>\n"
    )
    return plist_xml.encode("utf-8"), buf_nm.getvalue(), buf_hl.getvalue()


def patch_letter_png_bytes(fpath: Path) -> bytes:
    """Normalize IMG43 ('j' at 688,51,43,61) descender to y=8..52 (matching g/p/q/y) so it never crowds DOWN='《'."""
    img = Image.open(fpath).convert("RGBA")
    tile_box = (688, 51, 688 + 43, 51 + 61)
    tile = img.crop(tile_box)
    bbox = tile.getbbox()
    if bbox and bbox[3] > 52:
        glyph = tile.crop(bbox)
        gw = bbox[2] - bbox[0]
        target_h = 44  # y=8..52 matches 'g', 'p', 'q', 'y' bottom=52 in letter.png
        resized_glyph = glyph.resize((gw, target_h), Image.Resampling.LANCZOS)
        clean_tile = Image.new("RGBA", (43, 61), (0, 0, 0, 0))
        clean_tile.paste(resized_glyph, (bbox[0], 8))
        img.paste(clean_tile, tile_box)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def write_theme_tree(zf: zipfile.ZipFile, src_theme_dir: Path, zip_prefix: str, is_dark: bool, info_content: str):
    zf.writestr(f"{zip_prefix}/Info.txt", info_content.encode("utf-8"))

    reg = StyleRegistry()
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
                    patched_gen = patch_gen_ini(orientation, raw_text)
                    parsed_inis[rel_path] = parse_ini_sections(patched_gen)
                else:
                    parsed_inis[rel_path] = patch_layout_ini_pre_sizing(orientation, fpath.name, raw_text, reg)
                    if fpath.name.lower() == "py_9.ini":
                        parsed_inis[f"{orientation}/py_14.ini"] = patch_layout_ini_pre_sizing(
                            orientation, "py_14.ini", raw_text, reg
                        )

    # Build exact-size keycap atlas + .til + extra CSS styles and remap BACK_STYLE in parsed_inis
    anjian_png_bytes, anjian_til_text, extra_anjian_css = build_exact_keycaps_and_remap(src_theme_dir, parsed_inis)
    sym_nm_bytes, sym_hl_bytes, sym_til_text = build_sym_assets(src_theme_dir, is_dark=is_dark)

    written_inis = set()
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

            if fl == "aj.ogg":
                zf.write(AJ_AIFF, f"{zip_prefix}/{rel_root}/aj.aiff")
                continue
            if fl == "b.ogg":
                zf.write(B_AIFF, f"{zip_prefix}/{rel_root}/b.aiff")
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
            if rl in {"res/letter.png", "res/letter_p.png"}:
                zf.writestr(arc_path, patch_letter_png_bytes(fpath))
                continue

            if rel_path in parsed_inis:
                ini_text = serialize_ini_sections(parsed_inis[rel_path])
                zf.writestr(arc_path, ini_text.encode("utf-8"))
                written_inis.add(rel_path)
                continue

            ext = fpath.suffix.lower()
            if ext in TEXT_EXTS:
                text = decode_text_clean(fpath.read_bytes())
                if fl == "default.css":
                    text = patch_default_css(text, is_dark=is_dark, extra_anjian_css=extra_anjian_css, reg=reg)
                elif ext == ".til":
                    text = patch_other_til(fname, text)
                elif fl == "cand1.cnd":
                    ori = "port" if "port" in rel_root.lower() else "land"
                    text = patch_cand_cnd(ori, text)
                zf.writestr(arc_path, text.encode("utf-8"))
            else:
                zf.write(fpath, arc_path)

    # Write all newly generated layout INIs (py_14, jp_9, jp_daku, jp_kana, ru_jcuken, ru_caps)
    for extra_rel, sections in sorted(parsed_inis.items()):
        if extra_rel not in written_inis:
            ini_text = serialize_ini_sections(sections)
            zf.writestr(f"{zip_prefix}/{extra_rel}", ini_text.encode("utf-8"))

    plist_bytes, logo_nm_bytes, logo_hl_bytes = build_ios_logo_assets(src_theme_dir)
    zf.writestr(f"{zip_prefix}/res/logo/customImageConfig.plist", plist_bytes)
    zf.writestr(f"{zip_prefix}/res/logo/logo_Normal_iphone@3x.png", logo_nm_bytes)
    zf.writestr(f"{zip_prefix}/res/logo/logo_Highlight_iphone@3x.png", logo_hl_bytes)


def build_dual_bdi(src_dir: Path, out_bdi: Path, skin_name: str):
    info_txt = make_info_txt(skin_name, dual_mode=True, has_sound=True)
    out_bdi.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_bdi, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("skin/Info.txt", info_txt.encode("utf-8"))
        demo_path = src_dir / "demo.png"
        if demo_path.exists():
            zf.write(demo_path, "skin/demo.png")

        write_theme_tree(zf, src_dir / "light", "skin/light/skin", is_dark=False, info_content=info_txt)
        write_theme_tree(zf, src_dir / "dark", "skin/dark/skin", is_dark=True, info_content=info_txt)
    print(f"[OK] Built dual-mode iOS skin: {out_bdi} ({out_bdi.stat().st_size:,} bytes)")


if __name__ == "__main__":
    build_dual_bdi(
        EXTRACTED_DIR / "dark_14",
        OUT_DIR / "Write-森林集.bdi",
        "Write-森林集 (14键/26键智能深色)",
    )
