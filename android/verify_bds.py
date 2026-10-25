import re
import string
import sys
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BDS_PATH = Path(__file__).resolve().parent / "森林集-全能Pro.bds"
TEXT_EXTS = {".ini", ".cnd", ".css", ".til", ".txt", ".pop"}


def parse_ini_dict(text: str) -> dict:
    """Return {section_name: {key_upper: value}}."""
    out = {}
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1].strip()
            out.setdefault(cur, {})
        elif cur is not None and "=" in line and not s.startswith(";"):
            k, v = line.split("=", 1)
            out[cur][k.strip().upper()] = v.strip(" \t\r\n")
    return out


def collect_codes_from_bytes(raw: bytes):
    txt = raw.decode("utf-8")
    f_codes = set(re.findall(r"\bF\d+\b", txt))
    s_codes = set(re.findall(r"\bS\d+[_A-Z0-9]*\b", txt))
    return f_codes, s_codes


def verify_english_ascii_punctuation(ini: dict, css: dict, path: str):
    expected_down = {
        "w": ("€", "192"),
        "y": ("^", "185"), "u": ("[", "186"), "i": ("]", "187"), "o": ("~", "119"),
        "h": ("$", "113"), "j": ("|", "190"), "k": ("{", "188"), "l": ("}", "189"),
        "z": ('"', "178"), "x": ("\\", "179"), "c": ("+", "180"), "v": ("=", "181"),
        "b": ("`", "182"), "n": ("<", "183"), "m": (">", "184"),
    }
    extensions = set("€•°§")
    direct, popup = set(), set()
    primary_slots = defaultdict(list)
    for section, key in ini.items():
        if not re.fullmatch(r"(?:KEY|TIP)\d+", section):
            continue
        for action in ("CENTER", "SHOW", "UP", "DOWN", "LEFT", "RIGHT", "HOLD", "HOLDSYM"):
            value = key.get(action, "")
            # Full-width indentation spaces are an existing functional shortcut,
            # not punctuation; keep it rather than introducing trim-sensitive tabs.
            assert value.isspace() or all(c.isascii() or c in extensions for c in value), f"{path}/{section}/{action}: unexpected non-ASCII choice {value!r}"
            if action in {"CENTER", "UP", "DOWN", "LEFT", "RIGHT"}:
                direct.update(c for c in value if c in string.punctuation or c in extensions)
                if section.startswith("KEY") and (value == "..." or (len(value) == 1 and value in set(string.punctuation) | extensions)):
                    primary_slots[value].append(f"{section}/{action}")
        popup.update(c for c in key.get("HOLDSYM", "") if c in string.punctuation or c in extensions)
        letter = key.get("CENTER", "").lower()
        if letter in expected_down:
            symbol, style = expected_down[letter]
            assert key["DOWN"] == symbol, f"{path}/{section}: wrong English down swipe"
            assert key["FORE_STYLE"].split(",")[2] == style
            assert css["STYLE" + style]["SHOW"] == symbol
            assert symbol in key["HOLDSYM"]
        if section.startswith("KEY") and len(letter) == 1 and letter in string.ascii_lowercase:
            fore = key["FORE_STYLE"].split(",")
            for index, action in [(1, "UP"), (2, "DOWN")]:
                value = key.get(action, "")
                if value and not value.isspace() and not re.match(r"^[A-Z][\d+]", value):
                    assert css["STYLE" + fore[index]]["SHOW"] == value, f"{path}/{section}: mismatched {action} legend"
            # Follow actual state references rather than hardcoding remapped TIP IDs.
            for target in re.findall(r"S\d+_(\d+)", key.get("STAT_STYLE", "")):
                state = ini["TIP" + target]
                for action in ("UP", "DOWN", "LEFT", "RIGHT", "HOLDSYM"):
                    assert state.get(action) == key.get(action), f"{path}/{section}/TIP{target}: stale {action}"
                assert state["FORE_STYLE"].split(",")[1:] == fore[1:]
    assert set(string.punctuation) <= direct, f"{path}: missing direct symbols {set(string.punctuation)-direct}"
    assert set(string.punctuation) <= popup, f"{path}: missing popup symbols {set(string.punctuation)-popup}"
    assert extensions <= direct and extensions <= popup
    duplicates = {s: slots for s, slots in primary_slots.items() if len(slots) > 1}
    assert not duplicates, f"{path}: duplicate primary punctuation actions: {duplicates}"
    assert len(primary_slots) == 37, f"{path}: expected 32 ASCII + 4 extended + one ellipsis action"
    assert ini["KEY12"]["UP"] == "@" and ini["KEY20"]["DOWN"] == '"' and ini["KEY26"]["UP"] == "?"
    assert ini["KEY11"]["UP"] == "!"
    assert ini["KEY31"]["CENTER"] == "," and ini["KEY31"]["UP"] == "•" and ini["KEY31"]["DOWN"] == "..."
    assert ini["KEY31"]["FORE_STYLE"] == "666,193,191,135" and ini["KEY31"]["LEFT"] == "F42"
    assert css["STYLE666"]["SHOW"] == "," and css["STYLE191"]["SHOW"] == "..."
    assert css["STYLE192"]["SHOW"] == "€" and css["STYLE193"]["SHOW"] == "•"
    assert css["STYLE194"]["SHOW"] == "°" and css["STYLE195"]["SHOW"] == "§"
    assert ini["TIP3"]["CENTER"] == "F25" and ini["TIP3"]["FORE_STYLE"] == "912,193,191,135"
    assert ini["TIP3"]["UP"] == "•" and ini["TIP3"]["DOWN"] == "..." and ini["TIP3"]["LEFT"] == "F42"
    assert ini["KEY33"]["CENTER"] == "." and ini["KEY33"]["UP"] == "°" and ini["KEY33"]["DOWN"] == "§"
    assert ini["KEY33"]["FORE_STYLE"] == "72,194,195,136" and ini["KEY33"]["RIGHT"] == "F43"
    assert ini["KEY33"]["HOLDSYM"] == ".°§"


def verify_chinese_26_punctuation(ini: dict, css: dict, path: str):
    upper = {
        11: ("！", "215"), 12: ("@", "111"), 13: ("#", "112"), 14: ("%", "114"),
        16: ("&", "115"), 17: ("×", "207"), 18: ("（", "216"), 19: ("）", "217"),
        20: ("‘", "211"), 21: ("/", "120"), 22: ("-", "121"), 23: ("_", "122"),
        24: ("：", "218"), 25: ("；", "219"), 26: ("？", "220"),
    }
    down = {
        2: ("￥", "202"), 6: ("——", "203"), 7: ("【", "204"), 8: ("】", "205"), 9: ("～", "206"),
        16: ("$", "113"), 17: ("÷", "208"), 18: ("「", "209"), 19: ("」", "210"),
        20: ("“", "172"), 21: ("\\", "179"), 22: ("+", "180"), 23: ("=", "181"),
        24: ("·", "213"), 25: ("《", "175"), 26: ("》", "176"),
    }
    expected_symbols = {s for s, _ in upper.values()} | {s for s, _ in down.values()} | {"，", "。", "、", "……", "’", "”"}
    assert len(expected_symbols) == 37
    for action, index, expected in [("UP", 1, upper), ("DOWN", 2, down)]:
        for number, (symbol, style) in expected.items():
            key = ini[f"KEY{number}"]
            assert key[action] == symbol, f"{path}/KEY{number}: wrong {action} symbol"
            assert key["FORE_STYLE"].split(",")[index] == style
            assert css["STYLE" + style]["SHOW"] == symbol
            assert set(symbol) <= set(key["HOLDSYM"])
    for number, digit in enumerate("1234567890", 1):
        assert ini[f"KEY{number}"]["UP"] == digit
    slots = defaultdict(list)
    for section, key in ini.items():
        if not re.fullmatch(r"KEY\d+", section):
            continue
        for action in ("CENTER", "UP", "DOWN", "LEFT", "RIGHT"):
            value = key.get(action, "")
            if value and all(unicodedata.category(c)[0] in {"P", "S"} for c in value):
                slots[value].append(f"{section}/{action}")
        letter = key.get("CENTER", "")
        if len(letter) == 1 and letter in string.ascii_letters:
            allowed = {letter.lower(), letter.upper()}
            for action in ("CENTER", "SHOW", "UP", "DOWN", "LEFT", "RIGHT"):
                value = key.get(action, "")
                if not re.match(r"^[A-Z][\d+]", value):
                    allowed.update(c for c in value if not c.isspace())
            assert set(key["HOLDSYM"]) == allowed, f"{path}/{section}: stale long-press symbols"
            for target in re.findall(r"S\d+_(\d+)", key.get("STAT_STYLE", "")):
                state = ini["TIP" + target]
                for action in ("UP", "DOWN", "LEFT", "RIGHT", "HOLDSYM"):
                    assert state.get(action) == key.get(action), f"{path}/{section}: stale state TIP{target}"
    assert set(slots) == expected_symbols, f"{path}: incorrect symbol coverage {set(slots) ^ expected_symbols}"
    assert all(len(v) == 1 for v in slots.values()), f"{path}: duplicate primary punctuation actions {dict(slots)}"
    assert ini["KEY31"]["CENTER"] == "，" and ini["KEY31"]["UP"] == "、" and ini["KEY31"]["DOWN"] == "……"
    assert ini["KEY31"]["FORE_STYLE"] == "913,170,214,135" and ini["KEY31"]["LEFT"] == "F42"
    assert ini["KEY31"]["HOLDSYM"] == "，、…"
    assert ini["KEY33"]["CENTER"] == "。" and ini["KEY33"]["UP"] == "’" and ini["KEY33"]["DOWN"] == "”"
    assert ini["KEY33"]["FORE_STYLE"] == "914,212,173,136" and ini["KEY33"]["RIGHT"] == "F43"
    assert ini["KEY33"]["HOLDSYM"] == "。’”"


def main():
    strict_geometry = "--strict-geometry" in sys.argv
    assert BDS_PATH.exists(), f"Missing {BDS_PATH}"

    dst_f = set()
    dst_s = set()
    utf8_errors = 0
    backslash_paths = 0
    til_checks = 0
    exact_size_key_checks = 0

    with zipfile.ZipFile(BDS_PATH, "r") as zf:
        names = set(zf.namelist())
        for n in names:
            if "\\" in n:
                backslash_paths += 1

        required_paths = [
            "Info.txt",
            "demo.png",
            "light/res/default.css",
            "light/res/aj.ogg",
            "light/res/b.ogg",
            "light/port/gen.ini",
            "light/port/py_9.ini",
            "light/port/py_14.ini",
            "light/port/py_26.ini",
            "light/port/cand1.cnd",
            "light/port/symbol.ini",
            "light/port/sel_ch.ini",
            "light/port/sel_en.ini",
            "light/port/voice.ini",
            "dark/res/default.css",
            "dark/res/aj.ogg",
            "dark/res/b.ogg",
            "dark/port/gen.ini",
            "dark/port/py_9.ini",
            "dark/port/py_14.ini",
            "dark/port/py_26.ini",
            "dark/port/cand1.cnd",
            "dark/port/symbol.ini",
            "dark/port/sel_ch.ini",
            "dark/port/sel_en.ini",
            "dark/port/voice.ini",
        ]
        for rp in required_paths:
            assert rp in names, f"Missing required path in BDS: {rp}"

        aiff_files = [n for n in names if n.lower().endswith(".aiff")]
        assert not aiff_files, f"Found unexpected .aiff files in BDS: {aiff_files}"

        info_txt = zf.read("Info.txt").decode("utf-8")
        assert "Name=森林集-全能Pro" in info_txt, "Info.txt missing Name=森林集-全能Pro"
        assert "SupportPlatform=A" in info_txt, "Info.txt missing SupportPlatform=A"
        assert "Style=default" in info_txt, "Info.txt missing Style=default"
        assert "SupportDarkMode=1" in info_txt, "Info.txt missing SupportDarkMode=1"
        assert "Abilities=Sound" in info_txt, "Info.txt missing Abilities=Sound"
        assert "VersionCode=13" in info_txt, "Expected the matched Chinese 26-key punctuation release (v13)"

        for ogg_path in [
            "light/res/aj.ogg",
            "light/res/b.ogg",
            "dark/res/aj.ogg",
            "dark/res/b.ogg",
        ]:
            data = zf.read(ogg_path)
            assert data[:4] == b"OggS", f"Invalid OGG header in {ogg_path}"

        for theme in ["light", "dark"]:
            css_text = zf.read(f"{theme}/res/default.css").decode("utf-8")
            css = parse_ini_dict(css_text)

            style_headers = re.findall(r"^\[(STYLE\d+)\]", css_text, re.M)
            assert len(style_headers) == len(set(style_headers)), f"Duplicate STYLE sections in {theme}/res/default.css"
            assert css.get("STYLE300", {}).get("PRESS_SOUND_PATH") == "aj.ogg"
            assert css.get("STYLE301", {}).get("PRESS_SOUND_PATH") == "b.ogg"

            for req_st in [
                "STYLE130", "STYLE131", "STYLE132", "STYLE133", "STYLE134", "STYLE135", "STYLE136", "STYLE137",
                "STYLE140", "STYLE141", "STYLE142", "STYLE143",
                "STYLE144", "STYLE145", "STYLE146", "STYLE147", "STYLE148", "STYLE149",
                "STYLE170", "STYLE171", "STYLE172", "STYLE173", "STYLE174", "STYLE175", "STYLE176",
                "STYLE177",
                "STYLE247", "STYLE248", "STYLE249",
            ]:
                assert req_st in css, f"{theme}/res/default.css missing [{req_st}]"
            assert css["STYLE177"]["SHOW"] == "@"

            for sym_name in ["sym.til", "sym2.til"]:
                sym_til = parse_ini_dict(zf.read(f"{theme}/res/{sym_name}").decode("utf-8"))
                assert sym_til["GLOBAL"]["TILE_NUM"] == "19", f"{sym_name} TILE_NUM != 19"
                for idx in range(1, 20):
                    assert f"IMG{idx}" in sym_til, f"{sym_name} missing [IMG{idx}]"
                assert "IMG21" not in sym_til

            for st_idx, tile_idx in zip(range(620, 628), range(12, 20)):
                st_sec = css[f"STYLE{st_idx}"]
                assert st_sec["NM_IMG"] == f"sym,{tile_idx}"
                assert st_sec["HL_IMG"] == f"sym2,{tile_idx}"

            anjian_til = parse_ini_dict(zf.read(f"{theme}/res/anjian.til").decode("utf-8"))
            tile_num = int(anjian_til["GLOBAL"]["TILE_NUM"])
            for idx in range(1, tile_num + 1):
                sec = anjian_til.get(f"IMG{idx}")
                assert sec is not None, f"anjian.til missing [IMG{idx}]"
                assert sec.get("SCALE") == "1,1,1,1,1", f"anjian.til [IMG{idx}] missing SCALE=1,1,1,1,1"
                assert "INNER_RECT" in sec, f"anjian.til [IMG{idx}] missing INNER_RECT"

            # Check TILE_NUM >= max(IMGn) on all .til files
            for n in sorted(names):
                if n.startswith(f"{theme}/") and n.endswith(".til"):
                    til_doc = parse_ini_dict(zf.read(n).decode("utf-8"))
                    t_num = int(til_doc["GLOBAL"]["TILE_NUM"])
                    img_nums = [int(s[3:]) for s in til_doc if re.match(r"^IMG\d+$", s)]
                    assert t_num >= max(img_nums), f"{n} TILE_NUM={t_num} < max={max(img_nums)}"
                    til_checks += 1

            for ori in ["port", "land"]:
                gen = parse_ini_dict(zf.read(f"{theme}/{ori}/gen.ini").decode("utf-8"))
                for req_off in ["OFFSET23", "OFFSET24", "OFFSET25", "OFFSET32", "OFFSET33", "OFFSET35", "OFFSET144", "OFFSET145"]:
                    assert req_off in gen and "POS" in gen[req_off], f"{theme}/{ori}/gen.ini missing {req_off}"

                # Verify 14-key layouts (py_9.ini, py_14.ini, def_9.ini)
                for fn14 in ["py_9.ini", "py_14.ini", "def_9.ini"]:
                    ini14 = parse_ini_dict(zf.read(f"{theme}/{ori}/{fn14}").decode("utf-8"))
                    assert int(ini14["PANEL"]["KEY_NUM"]) == 22
                    # Row 1 (KEY1..5): menu(F31), @, del(F37), clear(F48), tab(　　)
                    assert ini14["KEY1"]["DOWN"] == "F31" and ini14["KEY1"]["FORE_STYLE"] == "1,2,100,620"
                    assert ini14["KEY2"]["DOWN"] == "@" and ini14["KEY2"]["FORE_STYLE"] == "3,4,101,177"
                    assert ini14["KEY2"]["HOLDSYM"] == "eErR2@"
                    assert ini14["KEY8"]["HOLDSYM"] == "gGhH"
                    assert ini14["KEY3"]["DOWN"] == "F37" and ini14["KEY3"]["FORE_STYLE"] == "5,6,102,145"
                    assert ini14["KEY4"]["DOWN"] == "F48" and ini14["KEY4"]["FORE_STYLE"] == "7,8,103,146"
                    assert ini14["KEY5"]["DOWN"] == "　　" and ini14["KEY5"]["FORE_STYLE"] == "9,10,104,134"
                    # GH must register both letters in action slots. SHOW alone
                    # is only a preview and is not read by KeyMap.b2.
                    assert ini14["KEY6"]["DOWN"] == "F47" and ini14["KEY6"]["FORE_STYLE"] == "11,12,105,130"
                    assert ini14["KEY7"]["DOWN"] == "F44" and ini14["KEY7"]["FORE_STYLE"] == "13,14,106,131"
                    assert (
                        ini14["KEY8"]["UP"] == "F49"
                        and ini14["KEY8"]["DOWN"] == "F50"
                        and ini14["KEY8"]["LEFT"] == "g"
                        and ini14["KEY8"]["RIGHT"] == "h"
                        and ini14["KEY8"]["CENTER"] == "g"
                        and ini14["KEY8"]["SHOW"] == "h"
                        and ini14["KEY8"]["FORE_STYLE"] == "15,16,140,143,141,142"
                    )
                    assert ini14["KEY9"]["DOWN"] == "F45" and ini14["KEY9"]["FORE_STYLE"] == "17,18,108,132"
                    assert ini14["KEY10"]["DOWN"] == "F46" and ini14["KEY10"]["FORE_STYLE"] == "19,109,133"
                    # Row 3 letter keys (KEY11..14 -> ZX, CV, BN, M): Chinese punctuation on UP and DOWN
                    assert ini14["KEY11"]["UP"] == "：" and ini14["KEY11"]["DOWN"] == "“"
                    assert ini14["KEY11"]["FORE_STYLE"] == "20,21,123,172"
                    assert ini14["KEY11"]["HOLDSYM"] == "zZxX：“"
                    assert ini14["KEY12"]["UP"] == "；" and ini14["KEY12"]["DOWN"] == "”"
                    assert ini14["KEY12"]["FORE_STYLE"] == "22,23,124,173"
                    assert ini14["KEY12"]["HOLDSYM"] == "cCvV；”"
                    # Keep gestures, keycap labels and native long-press choices in sync.
                    assert ini14["KEY13"]["UP"] == "8" and ini14["KEY13"]["DOWN"] == "（"
                    assert ini14["KEY13"]["FORE_STYLE"] == "24,25,107,117"
                    assert ini14["KEY13"]["HOLDSYM"] == "bBnN8（"
                    assert ini14["KEY14"]["UP"] == "？" and ini14["KEY14"]["DOWN"] == "）"
                    assert ini14["KEY14"]["FORE_STYLE"] == "26,125,118"
                    assert ini14["KEY14"]["HOLDSYM"] == "mM？）"
                    # Existing compact keycap glyphs use ASCII for these symbols.
                    assert css["STYLE117"]["SHOW"] == "(" and css["STYLE125"]["SHOW"] == "?"
                    assert css["STYLE124"]["SHOW"] == ";" and css["STYLE171"]["SHOW"] == "…"
                    assert css["STYLE172"]["SHOW"] == "“"
                    registered_letters = {
                        v for section, values in ini14.items() if section.startswith("KEY")
                        for k, v in values.items()
                        if k in {"UP", "CENTER", "RIGHT", "DOWN", "LEFT"}
                        and len(v) == 1 and "a" <= v <= "z"
                    }
                    assert set("abcdefghijklmnopqrstuvwxyz") <= registered_letters, (
                        f"{theme}/{ori}/{fn14}: letters missing from engine action slots: "
                        f"{set('abcdefghijklmnopqrstuvwxyz') - registered_letters}"
                    )
                    # Preserve native slide-delete: no DOWN/LEFT/RIGHT overrides.
                    assert (
                        ini14["KEY15"]["UP"] == "F1"
                        and ini14["KEY15"]["CENTER"] == "F11"
                        and ini14["KEY15"]["DOWN"] == "F61"
                        and ini14["KEY15"]["LEFT"] == "F51"
                        and ini14["KEY15"]["RIGHT"] == "F52"
                        and ini14["KEY15"]["FORE_STYLE"].endswith(",630")
                    )
                    assert (
                        ini14["KEY16"]["UP"] == "F48"
                        and "DOWN" not in ini14["KEY16"]
                        and "LEFT" not in ini14["KEY16"]
                        and "RIGHT" not in ini14["KEY16"]
                        and ini14["KEY16"]["CENTER"] == "F36"
                        and ini14["KEY16"]["HOLD"] == "F36"
                    )
                    # Row 4 (KEY17..22 -> 123, EN, ，, SPACE, 。, ENTER)
                    assert ini14["KEY17"]["UP"] == "F7"
                    assert ini14["KEY18"]["UP"] == "F84"
                    assert (
                        ini14["KEY19"]["LEFT"] == "F42"
                        and ini14["KEY19"]["UP"] == "、"
                        and ini14["KEY19"]["DOWN"] == "……"
                        and ini14["KEY19"]["FORE_STYLE"] == "913,170,171,135"
                        and ini14["KEY19"]["HOLDSYM"] == "，、…"
                    )
                    assert ini14["KEY20"]["UP"] == "F77"
                    assert (
                        ini14["KEY21"]["RIGHT"] == "F43"
                        and ini14["KEY21"]["UP"] == "！"
                        and ini14["KEY21"]["DOWN"] == "——"
                        and ini14["KEY21"]["FORE_STYLE"] == "914,110,174,136"
                    )
                    assert ini14["KEY22"]["UP"] == "F76" and ini14["KEY22"]["FORE_STYLE"] == "917,624"

                # Verify 26-key layouts (py_26.ini, def_26.ini, en_26.ini, en_26s.ini)
                for fn26 in ["py_26.ini", "def_26.ini", "en_26.ini", "en_26s.ini"]:
                    ini26 = parse_ini_dict(zf.read(f"{theme}/{ori}/{fn26}").decode("utf-8"))
                    is_en26 = fn26 in {"en_26.ini", "en_26s.ini"}
                    assert int(ini26["PANEL"]["KEY_NUM"]) == 34
                    # Row 1: Q=menu(F31), W=@, E=del(F37), R=clear(F48), T=tab(　　), P=hide(F8)
                    assert ini26["KEY1"]["DOWN"] == "F31"
                    assert ini26["KEY2"]["DOWN"] == ("€" if is_en26 else "￥")
                    assert ini26["KEY3"]["DOWN"] == "F37"
                    assert ini26["KEY4"]["DOWN"] == "F48"
                    assert ini26["KEY5"]["DOWN"] == "　　"
                    assert ini26["KEY10"]["DOWN"] == "F8"
                    # Row 2: A=select(F47), S=cut(F44), D=copy(F45), F=paste(F46), G=U/D/L/R
                    assert ini26["KEY11"]["DOWN"] == "F47"
                    assert ini26["KEY12"]["DOWN"] == "F44"
                    assert ini26["KEY13"]["DOWN"] == "F45"
                    assert ini26["KEY14"]["DOWN"] == "F46"
                    assert (
                        ini26["KEY15"]["UP"] == "F49"
                        and ini26["KEY15"]["DOWN"] == "F50"
                        and ini26["KEY15"]["LEFT"] == "F51"
                        and ini26["KEY15"]["RIGHT"] == "F52"
                    )
                    # Row 3 letter keys (Z..M -> KEY20..26), language-specific.
                    expected_r3_dn = dict(zip([f"KEY{i}" for i in range(20, 27)], ['"', "\\", "+", "=", "`", "<", ">"])) if is_en26 else {
                        "KEY20": "“",
                        "KEY21": "\\",
                        "KEY22": "+",
                        "KEY23": "=",
                        "KEY24": "·",
                        "KEY25": "《",
                        "KEY26": "》",
                    }
                    for k_r3, exp_punc in expected_r3_dn.items():
                        assert ini26[k_r3]["DOWN"] == exp_punc
                    assert (
                        ini26["KEY27"]["DOWN"] == "F61"
                        and ini26["KEY27"]["LEFT"] == "F51"
                        and ini26["KEY27"]["RIGHT"] == "F52"
                        and ini26["KEY27"]["FORE_STYLE"].endswith(",630")
                    )
                    assert (
                        ini26["KEY28"]["UP"] == "F48"
                        and "DOWN" not in ini26["KEY28"]
                        and "LEFT" not in ini26["KEY28"]
                        and "RIGHT" not in ini26["KEY28"]
                        and ini26["KEY28"]["CENTER"] == "F36"
                        and ini26["KEY28"]["HOLD"] == "F36"
                    )
                    # Row 4: KEY29(123), KEY30(EN/CH->F84), KEY31(，->LEFT=F42, UP=、, DOWN=……), KEY32(SPACE), KEY33(。->RIGHT=F43, UP=！, DOWN=——), KEY34(ENTER->clip F76)
                    assert ini26["KEY29"]["UP"] == "F7"
                    assert ini26["KEY30"]["UP"] == "F84"
                    if is_en26:
                        verify_english_ascii_punctuation(ini26, css, f"{theme}/{ori}/{fn26}")
                    else:
                        verify_chinese_26_punctuation(ini26, css, f"{theme}/{ori}/{fn26}")
                    assert ini26["KEY32"]["UP"] == "F77"
                    if not is_en26:
                        assert (
                            ini26["KEY33"]["RIGHT"] == "F43"
                            and ini26["KEY33"]["UP"] == "’"
                            and ini26["KEY33"]["DOWN"] == "”"
                            and ini26["KEY33"]["FORE_STYLE"] == "914,212,173,136"
                            and ini26["KEY33"]["CENTER"] == "。"
                            and ini26["KEY33"]["HOLDSYM"] == "。’”"
                        )
                    assert css["STYLE72"]["NM_IMG"] == "letter,72" and css["STYLE72"]["HL_IMG"] == "letter_p,72"
                    assert ini26["KEY34"]["UP"] == "F76" and ini26["KEY34"]["FORE_STYLE"] == "917,624"

                # Verify num_9.ini / num_26.ini
                for fn_num in ["num_9.ini", "num_26.ini"]:
                    ini_num = parse_ini_dict(zf.read(f"{theme}/{ori}/{fn_num}").decode("utf-8"))
                    assert ini_num["PANEL"].get("NO_BLUR") == "1"
                    assert ini_num["HINT"].get("LAYOUT_NAME") == "hint2" and "TYPE" not in ini_num["HINT"]
                    assert ini_num["KEY2"]["FORE_STYLE"] == "62,620,641,631,651" and ini_num["KEY2"]["POS_TYPE"] == "61,63,64,65,62"
                    assert ini_num["KEY6"]["CENTER"] == "5" and "SHOW" not in ini_num["KEY6"]
                    assert ini_num["KEY7"]["DOWN"] == "F46"
                    assert ini_num["KEY8"]["DOWN"] == "F42"
                    assert ini_num["KEY9"]["DOWN"] == "　　"
                    assert ini_num["KEY10"]["DOWN"] == "F43"
                    assert ini_num["KEY11"]["CENTER"] == "F36"
                    assert not ({"DOWN", "LEFT", "RIGHT"} & ini_num["KEY11"].keys())
                    assert ini_num["KEY13"]["CENTER"] == "=" and ini_num["KEY13"]["UP"] == "%" and ini_num["KEY13"]["DOWN"] == "¥"
                    assert ini_num["KEY16"]["DOWN"] == "F40" and ini_num["KEY16"]["UP"] == "⑩" and ini_num["KEY16"]["LEFT"] == "零"
                    assert ini_num["KEY17"]["DOWN"] == "F37" and ini_num["KEY17"]["UP"] == "，"
                    assert ini_num["KEY18"]["UP"] == "F76"

                # Verify contiguous KEY1..K and TIP1..T across all layout .ini files
                for n in sorted(names):
                    if not n.startswith(f"{theme}/{ori}/") or not n.endswith(".ini"):
                        continue
                    fname = Path(n).name.lower()
                    if fname in {"gen.ini", "logo.ini"}:
                        continue
                    ini_doc = parse_ini_dict(zf.read(n).decode("utf-8"))
                    key_secs = [s for s in ini_doc if re.match(r"^KEY\d+$", s)]
                    tip_secs = [s for s in ini_doc if re.match(r"^TIP\d+$", s)]
                    assert key_secs == [f"KEY{i}" for i in range(1, len(key_secs) + 1)], f"{n} non-contiguous KEYs"
                    assert tip_secs == [f"TIP{i}" for i in range(1, len(tip_secs) + 1)], f"{n} non-contiguous TIPs"
                    for ks in key_secs + tip_secs:
                        key = ini_doc[ks]
                        literal_chars = set()
                        for action in ("CENTER", "UP", "DOWN", "LEFT", "RIGHT"):
                            value = key.get(action, "")
                            if value and not re.match(r"^[A-Z][\d+]|^[A-Z]\+", value):
                                literal_chars.update(c for c in value if not c.isspace())
                        if literal_chars:
                            assert literal_chars <= set(key.get("HOLDSYM", "")), f"{n}/{ks}: a directional character is missing from long-press"
                        letters = {
                            c for action in ("CENTER", "UP", "DOWN", "LEFT", "RIGHT")
                            for c in key.get(action, "")
                            if len(key.get(action, "")) == 1 and c.isascii() and c.isalpha()
                        }
                        if letters:
                            popup = key.get("HOLDSYM", "")
                            for ch in letters:
                                assert ch.lower() in popup and ch.upper() in popup, f"{n}/{ks}: missing case variants for {ch}"
                            assert "HOLD" not in key, f"{n}/{ks}: immediate HOLD masks selectable popup"
                        if "HOLDSYM" in key:
                            assert len(key["HOLDSYM"]) == len(set(key["HOLDSYM"])), f"{n}/{ks}: duplicate popup choices"
                    if "PANEL" in ini_doc and key_secs:
                        assert int(ini_doc["PANEL"]["KEY_NUM"]) == len(key_secs)
                        assert int(ini_doc["PANEL"]["TIP_NUM"]) == len(tip_secs)

                    if strict_geometry:
                        for ks in key_secs:
                            kdict = ini_doc[ks]
                            vr = kdict.get("VIEW_RECT")
                            bs = kdict.get("BACK_STYLE")
                            if not vr or not bs or bs == "499":
                                continue
                            _, _, kw, kh = [int(x) for x in vr.split(",")]
                            st_sec = css.get(f"STYLE{bs}")
                            assert st_sec is not None, f"{n} [{ks}] BACK_STYLE={bs} missing in default.css"
                            for img_key in ("NM_IMG", "HL_IMG"):
                                img_ref = st_sec.get(img_key, "")
                                if img_ref.startswith("anjian,"):
                                    t_idx = int(img_ref.split(",")[1])
                                    sr = anjian_til[f"IMG{t_idx}"]["SOURCE_RECT"]
                                    _, _, tw, th = [int(x) for x in sr.split(",")]
                                    assert (tw, th) == (kw, kh), (
                                        f"{n} [{ks}] {img_key}={img_ref} size ({tw},{th}) != VIEW_RECT ({kw},{kh})"
                                    )
                                    exact_size_key_checks += 1

        # Check all text files are valid UTF-8 with no U+FFFD
        for n in sorted(names):
            ext = Path(n).suffix.lower()
            if ext in TEXT_EXTS:
                raw = zf.read(n)
                try:
                    decoded = raw.decode("utf-8")
                    if "\ufffd" in decoded:
                        utf8_errors += 1
                except UnicodeDecodeError:
                    utf8_errors += 1
                f, s = collect_codes_from_bytes(raw)
                dst_f |= f
                dst_s |= s

    assert utf8_errors == 0, f"Found {utf8_errors} UTF-8 errors"
    assert backslash_paths == 0, f"Found {backslash_paths} backslash paths"

    required_f_codes = {
        "F1", "F4", "F6", "F7", "F8", "F9", "F11", "F14", "F16",
        "F22", "F23", "F25", "F26", "F27", "F31", "F36", "F37", "F38", "F39", "F40",
        "F42", "F43", "F44", "F45", "F46", "F47", "F48",
        "F49", "F50", "F51", "F52", "F55", "F72", "F76", "F77", "F80", "F84",
    }
    missing_f = required_f_codes - dst_f
    assert not missing_f, f"Missing required F codes: {missing_f}"

    print(
        f"ALL_BDS_CHECKS_PASSED: files={len(names)}, f_codes={len(dst_f)}, "
        f"s_codes={len(dst_s)}, til_checks={til_checks}, utf8_errors={utf8_errors}, backslash_paths={backslash_paths}"
    )


if __name__ == "__main__":
    main()
