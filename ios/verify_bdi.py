import re
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BDI_PATH = Path(__file__).resolve().parent / "Write-森林集.bdi"
TEXT_EXTS = {".ini", ".cnd", ".css", ".til", ".txt", ".plist"}

# iOS-unsupported F-codes and style IDs that MUST NEVER appear in any layout .ini
FORBIDDEN_IOS_FCODES = {"F44", "F45", "F46", "F47", "F66", "F67", "F72", "F76", "F77"}
FORBIDDEN_INI_FCODES = FORBIDDEN_IOS_FCODES | {"F31", "F37", "F40"}
FORBIDDEN_FORE_STYLES = {"75", "130", "131", "132", "133", "621", "624", "627", "919"}
FORBIDDEN_KEY_LABELS = {"menu", "undo", "esc", "del", "常用语"}


def parse_ini_dict(text: str) -> dict:
    """Return {section_name: {key_upper: value}}. Preserves U+2028 intact."""
    out = {}
    cur = None
    for raw_line in text.rstrip("\r\n").split("\n"):
        line = raw_line.rstrip("\r")
        s = line.strip(" \t\r\n")
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1].strip(" \t\r\n")
            out.setdefault(cur, {})
        elif cur is not None and "=" in line and not s.startswith(";"):
            k, v = line.split("=", 1)
            out[cur][k.strip(" \t\r\n").upper()] = v.strip(" \t\r\n")
    return out


def collect_codes_from_bytes(raw: bytes):
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        txt = raw.decode("gb18030", errors="ignore")
    f_codes = set(re.findall(r"\bF\d+\b", txt))
    s_codes = set(re.findall(r"\bS\d+[_A-Z0-9]*\b", txt))
    return f_codes, s_codes


def main():
    strict_geometry = "--strict-geometry" in sys.argv
    assert BDI_PATH.exists(), f"Missing {BDI_PATH}"

    dst_f = set()
    utf8_errors = 0
    backslash_paths = 0
    exact_size_key_checks = 0
    style_pos_checks = 0

    with zipfile.ZipFile(BDI_PATH, "r") as zf:
        names = set(zf.namelist())
        for n in names:
            if "\\" in n:
                backslash_paths += 1

        # 1. Verify required dual-mode BDI paths + iOS custom logo + absence of removed JP/RU layouts
        for theme in ["light", "dark"]:
            for ori in ["port", "land"]:
                for fn in [
                    "gen.ini",
                    "py_9.ini",
                    "py_14.ini",
                    "py_26.ini",
                    "en_26.ini",
                    "en_9.ini",
                    "num_9.ini",
                    "num_26.ini",
                    "symbol.ini",
                    "sel_ch.ini",
                    "sel_en.ini",
                    "cand1.cnd",
                ]:
                    rp = f"skin/{theme}/skin/{ori}/{fn}"
                    assert rp in names, f"Missing required path in BDI: {rp}"
                for removed_fn in ["jp_9.ini", "jp_daku.ini", "jp_kana.ini", "ru_jcuken.ini", "ru_caps.ini"]:
                    rp_rem = f"skin/{theme}/skin/{ori}/{removed_fn}"
                    assert rp_rem not in names, f"Removed layout still present in BDI: {rp_rem}"

        ogg_files = [n for n in names if n.lower().endswith(".ogg")]
        assert not ogg_files, f"Found unexpected .ogg files: {ogg_files}"

        # 2. Verify Info.txt content
        for info_path in ["skin/Info.txt", "skin/light/skin/Info.txt", "skin/dark/skin/Info.txt"]:
            txt = zf.read(info_path).decode("utf-8")
            assert "SupportPlatform=I" in txt, f"{info_path} missing SupportPlatform=I"
            assert "SupportDarkMode=1" in txt, f"{info_path} missing SupportDarkMode=1"
            assert "Abilities=Sound" in txt, f"{info_path} missing Abilities=Sound"
            assert "Style=" not in txt, f"{info_path} still contains Android Style="

        # 3. Verify AIFF headers
        for aiff_path in [
            "skin/light/skin/res/aj.aiff",
            "skin/light/skin/res/b.aiff",
            "skin/dark/skin/res/aj.aiff",
            "skin/dark/skin/res/b.aiff",
        ]:
            data = zf.read(aiff_path)
            assert data[:4] == b"FORM" and data[8:12] == b"AIFF", f"Invalid AIFF header in {aiff_path}"

        # 4. Per-theme deep verification
        for theme in ["light", "dark"]:
            prefix = f"skin/{theme}/skin"
            css_text = zf.read(f"{prefix}/res/default.css").decode("utf-8")
            css = parse_ini_dict(css_text)

            style_headers = re.findall(r"^\[(STYLE\d+)\]", css_text, re.M)
            assert len(style_headers) == len(set(style_headers)), f"Duplicate STYLE sections in {prefix}/res/default.css"
            assert css.get("STYLE300", {}).get("PRESS_SOUND_PATH") == "aj.aiff"
            assert css.get("STYLE301", {}).get("PRESS_SOUND_PATH") == "b.aiff"
            assert ".ogg" not in css_text.lower()

            for sym_name in ["sym.til", "sym2.til"]:
                sym_til = parse_ini_dict(zf.read(f"{prefix}/res/{sym_name}").decode("utf-8"))
                assert sym_til["GLOBAL"]["TILE_NUM"] == "19", f"{sym_name} TILE_NUM != 19"
                for idx in range(1, 20):
                    assert f"IMG{idx}" in sym_til, f"{sym_name} missing [IMG{idx}]"

            anjian_til = parse_ini_dict(zf.read(f"{prefix}/res/anjian.til").decode("utf-8"))
            tile_num = int(anjian_til["GLOBAL"]["TILE_NUM"])
            for idx in range(1, tile_num + 1):
                sec = anjian_til.get(f"IMG{idx}")
                assert sec is not None, f"anjian.til missing [IMG{idx}]"
                assert sec.get("SCALE") == "1,1,1,1,1", f"anjian.til [IMG{idx}] missing SCALE=1,1,1,1,1"
                assert "INNER_RECT" in sec, f"anjian.til [IMG{idx}] missing INNER_RECT"

            for ori in ["port", "land"]:
                cnd = parse_ini_dict(zf.read(f"{prefix}/{ori}/cand1.cnd").decode("utf-8"))
                assert cnd["CAND"]["ICON_NUM"] == "4"
                assert cnd["ICON1"].get("FIX_SIZE") == "45,37"

                gen = parse_ini_dict(zf.read(f"{prefix}/{ori}/gen.ini").decode("utf-8"))
                assert gen["LIST"]["LIST_ORDER"] == "0"
                assert "NAMES" not in gen["LIST"] and "VALUES" not in gen["LIST"], (
                    f"{ori}/gen.ini [LIST] must not define default NAMES/VALUES"
                )
                assert (
                    gen["MORE"].get("GRID") == "4,5"
                    and gen["MORE"].get("LAYOUT_NAME") == "sel_ch"
                    and gen["MORE"].get("SYM_LAYOUT") == "symbol"
                    and gen["MORE"].get("FORE_STYLE") == "154"
                ), f"{ori}/gen.ini [MORE] must have GRID=4,5, LAYOUT_NAME=sel_ch, SYM_LAYOUT=symbol, FORE_STYLE=154"
                assert "SCAND" in gen and gen["SCAND"].get("BACK_STYLE") == "922"
                assert "LOGO" in gen and gen["LOGO"].get("LAYOUT_NAME") == "logo"
                for req_off in ["OFFSET1", "OFFSET2", "OFFSET3", "OFFSET4", "OFFSET5",
                                "OFFSET140", "OFFSET141", "OFFSET142", "OFFSET143", "OFFSET144", "OFFSET145",
                                "OFFSET146", "OFFSET147", "OFFSET148", "OFFSET149", "OFFSET150", "OFFSET151"]:
                    assert req_off in gen and gen[req_off].get("POS"), f"{ori}/gen.ini missing {req_off}"

                exp_cand_rect = "0,0,1125,114" if ori == "port" else "0,0,1920,139"
                exp_panel_size = "1125,536" if ori == "port" else "1920,720"

                # Verify 14-key layouts (py_9.ini, py_14.ini, def_9.ini)
                for fn14 in ["py_9.ini", "py_14.ini", "def_9.ini"]:
                    ini14 = parse_ini_dict(zf.read(f"{prefix}/{ori}/{fn14}").decode("utf-8"))
                    assert ini14["PANEL"].get("SIZE") == exp_panel_size
                    assert ini14["CAND"].get("VIEW_RECT") == exp_cand_rect and ini14["CAND"].get("LAYOUT_NAME") == "cand1"
                    # Row 1 (KEY1..KEY5): —, …, ~, clear(F48), tab(F41)
                    assert ini14["KEY1"]["DOWN"] == "—"
                    assert ini14["KEY2"]["DOWN"] == "…"
                    assert ini14["KEY3"]["DOWN"] == "~"
                    assert ini14["KEY4"]["DOWN"] == "F48"
                    assert ini14["KEY5"]["DOWN"] == "F41"
                    # Row 2 (KEY11..KEY15): AS=@, DF=、, GH=(UP=8, DOWN=·), JK=；, L=(UP=0, DOWN=/)
                    assert ini14["KEY11"]["DOWN"] == "@"
                    assert ini14["KEY12"]["DOWN"] == "、"
                    assert (
                        ini14["KEY13"]["UP"] == "8"
                        and ini14["KEY13"]["DOWN"] == "·"
                        and ini14["KEY13"]["LEFT"] == "g"
                        and ini14["KEY13"]["RIGHT"] == "h"
                    )
                    assert ini14["KEY14"]["DOWN"] == "；"
                    assert ini14["KEY15"]["UP"] == "0" and ini14["KEY15"]["DOWN"] == "/"
                    # Row 3 letter keys (KEY20..KEY23): ZX=！, CV=：, BN=%, M=？; NO DOWN=
                    assert ini14["KEY22"]["UP"] == "%"
                    for k_r3 in ["KEY20", "KEY21", "KEY22", "KEY23"]:
                        assert "DOWN" not in ini14[k_r3], f"{ori}/{fn14} {k_r3} still has DOWN="
                    # sym (KEY27) & BACK (KEY28: NO DOWN=del!)
                    assert ini14["KEY27"]["CENTER"] == "F1" and ini14["KEY27"]["UP"] == "F1" and ini14["KEY27"]["DOWN"] == "'"
                    assert ini14["KEY28"]["UP"] == "F48" and "DOWN" not in ini14["KEY28"]
                    # Row 4: KEY30 (EN) -> CENTER=F16 (CH<->EN), NO UP/DOWN; KEY32 (SPACE) -> NO UP; KEY34 (ENTER) -> UP=\u2028
                    assert ini14["KEY30"]["CENTER"] == "F16" and "UP" not in ini14["KEY30"] and "DOWN" not in ini14["KEY30"]
                    assert ini14["KEY31"]["UP"] == "F42"
                    assert (
                        "UP" not in ini14["KEY32"]
                        and ini14["KEY32"]["LEFT"] == "F51"
                        and ini14["KEY32"]["RIGHT"] == "F52"
                        and ini14["KEY32"]["POS_TYPE"] == "1,150,151"
                    )
                    assert ini14["KEY33"]["UP"] == "F43"
                    assert ini14["KEY34"]["UP"] == "\u2028" and ini14["KEY34"]["HOLD"] == "\u2028"

                # Verify 26-key layouts (py_26.ini, def_26.ini, en_26.ini, en_26s.ini)
                for fn26 in ["py_26.ini", "def_26.ini", "en_26.ini", "en_26s.ini"]:
                    ini26 = parse_ini_dict(zf.read(f"{prefix}/{ori}/{fn26}").decode("utf-8"))
                    assert ini26["PANEL"].get("SIZE") == exp_panel_size
                    assert ini26["CAND"].get("VIEW_RECT") == exp_cand_rect and ini26["CAND"].get("LAYOUT_NAME") == "cand1"
                    # Row 1: Q=…, W=·, E==, R=clear(F48), T=tab(F41), Y=、, U=；, I=—, O=「, P=」
                    assert ini26["KEY1"]["DOWN"] == "…"
                    assert ini26["KEY2"]["DOWN"] == "·"
                    assert ini26["KEY3"]["DOWN"] == "="
                    assert ini26["KEY4"]["DOWN"] == "F48"
                    assert ini26["KEY5"]["DOWN"] == "F41"
                    assert ini26["KEY6"]["DOWN"] == "、"
                    assert ini26["KEY7"]["DOWN"] == "；"
                    assert ini26["KEY8"]["DOWN"] == "—"
                    assert ini26["KEY9"]["DOWN"] == "「"
                    assert ini26["KEY10"]["DOWN"] == "」"
                    # Row 2: A=+, S=home(F42), D=end(F43), F=¥, G=(UP=%, DOWN=·), H=%, J=《, K=》, L=：
                    assert ini26["KEY11"]["DOWN"] == "+"
                    assert ini26["KEY12"]["DOWN"] == "F42"
                    assert ini26["KEY13"]["DOWN"] == "F43"
                    assert ini26["KEY14"]["UP"] == "$" and ini26["KEY14"]["DOWN"] == "¥"
                    assert "LEFT" not in ini26["KEY14"] and "RIGHT" not in ini26["KEY14"], f"{fn26} F key still has redundant cursor!"
                    assert (
                        ini26["KEY15"]["UP"] == "%"
                        and ini26["KEY15"]["DOWN"] == "·"
                        and "LEFT" not in ini26["KEY15"]
                        and "RIGHT" not in ini26["KEY15"]
                    )
                    assert ini26["KEY16"]["UP"] == "&" and ini26["KEY16"]["DOWN"] == "%"
                    assert "LEFT" not in ini26["KEY16"] and "RIGHT" not in ini26["KEY16"], f"{fn26} H key still has redundant cursor!"
                    assert ini26["KEY17"]["UP"] == "*" and ini26["KEY17"]["DOWN"] == "《" and ini26["KEY17"]["POS_TYPE"] == "1,2,3"
                    assert ini26["KEY18"]["DOWN"] == "》"
                    assert ini26["KEY19"]["UP"] == ")" and ini26["KEY19"]["DOWN"] == "："
                    assert ini26["KEY27"]["UP"] == "F1"
                    assert ini26["KEY28"]["UP"] == "F48" and "DOWN" not in ini26["KEY28"]
                    # Language key on KEY30: Tap toggles CH<->EN only (no UP/DOWN)
                    if fn26 in {"en_26.ini", "en_26s.ini"}:
                        assert ini26["KEY30"]["CENTER"] == "F15" and "UP" not in ini26["KEY30"] and "DOWN" not in ini26["KEY30"]
                        assert ini26["KEY17"].get("STAT_STYLE") == "S1_27|S2_27", f"{fn26} KEY17 STAT_STYLE mismatch"
                        assert (
                            ini26["TIP27"]["UP"] == "*"
                            and ini26["TIP27"]["DOWN"] == "《"
                            and ini26["TIP27"]["POS_TYPE"] == "1,2,3"
                            and len(ini26["TIP27"]["FORE_STYLE"].split(",")) == 3
                        ), f"{fn26} TIP27 (J uppercase state) missing DOWN=《"
                    else:
                        assert ini26["KEY30"]["CENTER"] == "F16" and "UP" not in ini26["KEY30"] and "DOWN" not in ini26["KEY30"]
                    assert (
                        "UP" not in ini26["KEY32"]
                        and ini26["KEY32"]["LEFT"] == "F51"
                        and ini26["KEY32"]["RIGHT"] == "F52"
                        and ini26["KEY32"]["POS_TYPE"] == "1,150,151"
                    )
                    assert ini26["KEY34"]["UP"] == "\u2028" and ini26["KEY34"]["HOLD"] == "\u2028"

                # Verify num_9.ini / num_26.ini
                for fn_num in ["num_9.ini", "num_26.ini"]:
                    ini_num = parse_ini_dict(zf.read(f"{prefix}/{ori}/{fn_num}").decode("utf-8"))
                    assert ini_num["PANEL"].get("NO_BLUR") == "1"
                    assert ini_num["PANEL"].get("SIZE") == exp_panel_size
                    assert ini_num["CAND"].get("VIEW_RECT") == exp_cand_rect and ini_num["CAND"].get("LAYOUT_NAME") == "cand1"
                    assert ini_num["KEY2"]["DOWN"] == "~" and ini_num["KEY2"]["UP"] == "①" and ini_num["KEY2"]["LEFT"] == "壹"
                    assert ini_num["KEY3"]["DOWN"] == "…" and ini_num["KEY3"]["UP"] == "②" and ini_num["KEY3"]["LEFT"] == "贰"
                    assert ini_num["KEY4"]["DOWN"] == "#" and ini_num["KEY4"]["UP"] == "③" and ini_num["KEY4"]["LEFT"] == "叁"
                    assert ini_num["KEY5"]["DOWN"] == "F42" and ini_num["KEY5"]["UP"] == "④" and ini_num["KEY5"]["LEFT"] == "肆"
                    assert ini_num["KEY6"]["DOWN"] == "F41" and ini_num["KEY6"]["UP"] == "⑤" and ini_num["KEY6"]["LEFT"] == "伍"
                    assert ini_num["KEY7"]["DOWN"] == "F43" and ini_num["KEY7"]["UP"] == "⑥" and ini_num["KEY7"]["LEFT"] == "陆"
                    assert ini_num["KEY8"]["DOWN"] == ":" and ini_num["KEY8"]["UP"] == "⑦" and ini_num["KEY8"]["LEFT"] == "柒"
                    assert ini_num["KEY9"]["DOWN"] == "-" and ini_num["KEY9"]["UP"] == "⑧" and ini_num["KEY9"]["LEFT"] == "捌"
                    assert ini_num["KEY10"]["DOWN"] == "/" and ini_num["KEY10"]["UP"] == "⑨" and ini_num["KEY10"]["LEFT"] == "玖"
                    assert ini_num["KEY11"]["UP"] == "F48" and "DOWN" not in ini_num["KEY11"]
                    assert ini_num["KEY12"]["CENTER"] == "F1" and ini_num["KEY12"]["UP"] == "F1" and ini_num["KEY12"]["DOWN"] == "F7"
                    assert ini_num["KEY13"]["CENTER"] == "=" and ini_num["KEY13"]["UP"] == "%" and ini_num["KEY13"]["DOWN"] == "¥"
                    assert (
                        "UP" not in ini_num["KEY15"]
                        and ini_num["KEY15"]["LEFT"] == "F51"
                        and ini_num["KEY15"]["RIGHT"] == "F52"
                        and ini_num["KEY15"]["POS_TYPE"] == "1,148,149"
                    )
                    assert ini_num["KEY16"]["CENTER"] == "0" and ini_num["KEY16"]["UP"] == "⑩" and ini_num["KEY16"]["LEFT"] == "零" and ini_num["KEY16"]["DOWN"] == "00"
                    assert ini_num["KEY17"]["CENTER"] == "." and ini_num["KEY17"]["UP"] == "，" and ini_num["KEY17"]["DOWN"] == "。"
                    assert ini_num["KEY18"]["UP"] == "\u2028" and ini_num["KEY18"]["HOLD"] == "\u2028"

                # Verify sel_ch.ini, sel_en.ini, symbol.ini, help.ini keep INPUT and empty CAND
                for fn_full in ["sel_ch.ini", "sel_en.ini", "symbol.ini", "help.ini"]:
                    ini_full = parse_ini_dict(zf.read(f"{prefix}/{ori}/{fn_full}").decode("utf-8"))
                    assert "INPUT" in ini_full and not ini_full.get("CAND"), f"{fn_full} must keep INPUT and empty CAND"
                    if fn_full in {"sel_ch.ini", "sel_en.ini", "symbol.ini"}:
                        assert not ini_full.get("MORE"), f"{fn_full} [MORE] must be empty so it inherits gen.ini"
                        assert "NAMES" not in ini_full.get("LIST", {}) and "VALUES" not in ini_full.get("LIST", {}), (
                            f"{fn_full} [LIST] must not override category tabs with static NAMES/VALUES"
                        )
                        assert ini_full["KEY11"].get("CENTER") == "F4" and ini_full["KEY15"].get("CENTER") == "F36"

                # Verify ALL .ini files in this orientation:
                #   1. Contiguous KEY1..K and TIP1..T
                #   2. Zero forbidden iOS F-codes (including F31/F37/F40) or forbidden FORE_STYLE IDs
                #   3. Every FORE_STYLE item has a valid STYLE in default.css and never shows menu/undo/esc/del
                #   4. Exact 1:1 keycap tile sizes
                for n in sorted(names):
                    if not n.startswith(f"{prefix}/{ori}/") or not n.endswith(".ini"):
                        continue
                    fname = Path(n).name.lower()
                    if fname in {"gen.ini", "logo.ini"}:
                        continue
                    raw_ini_text = zf.read(n).decode("utf-8")
                    ini_doc = parse_ini_dict(raw_ini_text)

                    # Check forbidden F-codes and forbidden Z+ main-keyboard or JP/RU transitions in layout INIs
                    f_in_ini = set(re.findall(r"\bF\d+\b", raw_ini_text))
                    bad_f = f_in_ini & FORBIDDEN_INI_FCODES
                    assert not bad_f, f"{n} contains forbidden/broken iOS F-codes: {bad_f}"
                    bad_z = set(re.findall(r"Z\+(?:py_9|py_14|py_26|def_9|def_26|en_26|en_26s|en_9|en_9s|jp_|ru_)\w*", raw_ini_text))
                    assert not bad_z, f"{n} contains forbidden Z+ layout transition: {bad_z}"

                    key_secs = [s for s in ini_doc if re.match(r"^KEY\d+$", s) and any(k != "INFO" for k in ini_doc[s])]
                    tip_secs = [s for s in ini_doc if re.match(r"^TIP\d+$", s) and any(k != "INFO" for k in ini_doc[s])]
                    assert not [s for s in ini_doc if re.match(r"^KEY_TOP_\d+$", s, re.I)], f"{n} still has Android KEY_TOP_* sections"
                    if fname in {"sel_ch.ini", "sel_en.ini", "symbol.ini"}:
                        assert key_secs == ["KEY1", "KEY2", "KEY3", "KEY11", "KEY12", "KEY13", "KEY14", "KEY15"], (
                            f"{n} unexpected KEY sections: {key_secs}"
                        )

                    for ks in key_secs:
                        stat_val = ini_doc[ks].get("STAT_STYLE", "")
                        for m in re.finditer(r"S\d+_(\d+)", stat_val):
                            t_ref = f"TIP{m.group(1)}"
                            assert t_ref in tip_secs, f"{n} [{ks}] STAT_STYLE={stat_val} points to missing/empty [{t_ref}]"
                            if "DOWN" in ini_doc[ks]:
                                assert ini_doc[t_ref].get("DOWN") == ini_doc[ks]["DOWN"], (
                                    f"{n} [{ks}] DOWN={ini_doc[ks]['DOWN']} != [{t_ref}] DOWN={ini_doc[t_ref].get('DOWN')}"
                                )

                    for sec_k in key_secs + tip_secs:
                        kdict = ini_doc[sec_k]
                        fs_raw = kdict.get("FORE_STYLE", "")
                        if fs_raw:
                            fs_list = [x.strip() for x in fs_raw.split(",") if x.strip()]
                            bad_st = set(fs_list) & FORBIDDEN_FORE_STYLES
                            assert not bad_st, f"{n} [{sec_k}] contains forbidden FORE_STYLE: {bad_st}"
                            for st_id in fs_list:
                                assert f"STYLE{st_id}" in css, f"{n} [{sec_k}] references missing STYLE{st_id}"
                                show_lbl = css[f"STYLE{st_id}"].get("SHOW", "")
                                assert show_lbl not in FORBIDDEN_KEY_LABELS, (
                                    f"{n} [{sec_k}] STYLE{st_id} still displays forbidden label SHOW={show_lbl}"
                                )
                            pt_raw = kdict.get("POS_TYPE", "")
                            if pt_raw:
                                pt_list = [x.strip() for x in pt_raw.split(",") if x.strip()]
                                assert len(pt_list) >= len(fs_list), (
                                    f"{n} [{sec_k}] POS_TYPE ({pt_raw}) shorter than FORE_STYLE ({fs_raw})"
                                )
                                style_pos_checks += 1

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
                            for img_key in ["NM_IMG", "HL_IMG"]:
                                img_ref = st_sec.get(img_key, "")
                                assert img_ref.startswith("anjian,"), f"{n} [{ks}] STYLE{bs} {img_key}={img_ref}"
                                t_id = img_ref.split(",")[1]
                                t_rect = anjian_til[f"IMG{t_id}"]["SOURCE_RECT"]
                                _, _, tw, th = [int(x) for x in t_rect.split(",")]
                                assert (tw, th) == (kw, kh), (
                                    f"Size mismatch in {n} [{ks}]: key=({kw},{kh}) vs tile IMG{t_id}=({tw},{th})"
                                )
                                exact_size_key_checks += 1

        # 5. Verify all text files are strict UTF-8 without U+FFFD
        for n in names:
            ext = Path(n).suffix.lower()
            if ext in TEXT_EXTS:
                raw = zf.read(n)
                try:
                    txt = raw.decode("utf-8")
                    if "\ufffd" in txt:
                        utf8_errors += 1
                except UnicodeDecodeError:
                    utf8_errors += 1
                f, _ = collect_codes_from_bytes(raw)
                dst_f |= f

    assert utf8_errors == 0, f"utf8_errors={utf8_errors}"
    assert backslash_paths == 0, f"backslash_paths={backslash_paths}"
    assert not (dst_f & FORBIDDEN_IOS_FCODES), f"Found forbidden iOS F-codes in package: {dst_f & FORBIDDEN_IOS_FCODES}"

    print(
        f"ALL_CHECKS_PASSED: files={len(names)}, f_codes={len(dst_f)}, "
        f"style_pos_checks={style_pos_checks}, exact_size_key_checks={exact_size_key_checks}, "
        f"utf8_errors={utf8_errors}, backslash_paths={backslash_paths}"
    )


if __name__ == "__main__":
    main()
