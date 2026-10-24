import os
from collections import defaultdict

BUILD_DIR = r"C:\Users\Administrator\baiduime\_build"
RIME_8105 = os.path.join(BUILD_DIR, "rime_8105.yaml")
RIME_BASE = os.path.join(BUILD_DIR, "rime_base.yaml")
OUT_DICT = os.path.join(BUILD_DIR, "pinyin_dict.txt")

CUSTOM_BOOST = [
    ("提升体验", "ti'sheng'ti'yan", 999999),
    ("森林集", "sen'lin'ji", 999999),
    ("微软双拼", "wei'ruan'shuang'pin", 999999),
    ("双拼方案", "shuang'pin'fang'an", 999999),
    ("分体键盘", "fen'ti'jian'pan", 999999),
    ("物理键盘", "wu'li'jian'pan", 999999),
    ("机械键盘", "ji'xie'jian'pan", 999999),
    ("小米平板", "xiao'mi'ping'ban", 999999),
    ("百度输入法", "bai'du'shu'ru'fa", 999999),
    ("自研输入法", "zi'yan'shu'ru'fa", 999999),
    ("语音输入", "yu'yin'shu'ru", 999999),
    ("组合键", "zu'he'jian", 999999),
    ("候选词", "hou'xuan'ci", 999999),
    ("大写锁定", "da'xie'suo'ding", 999999),
]

def main():
    entries = defaultdict(dict)  # py -> {word: weight}

    for word, py, wt in CUSTOM_BOOST:
        entries[py][word] = max(entries[py].get(word, 0), wt)

    # 1. Load 8105 single characters
    with open(RIME_8105, "r", encoding="utf-8") as f:
        in_body = False
        for line in f:
            line = line.strip()
            if line == "...":
                in_body = True
                continue
            if not in_body or not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                w = parts[0].strip()
                py = parts[1].strip().replace(" ", "'")
                wt = int(parts[2]) if len(parts) >= 3 and parts[2].strip().isdigit() else 1
                if len(w) == 1 and ord(w) < 0x10000 and wt >= 1:
                    # Normalize single-char weight to a reasonable scale while preserving order
                    norm_wt = max(10, wt // 100)
                    entries[py][w] = max(entries[py].get(w, 0), norm_wt)

    # 2. Load rime_base words
    with open(RIME_BASE, "r", encoding="utf-8") as f:
        in_body = False
        for line in f:
            line = line.strip()
            if line == "...":
                in_body = True
                continue
            if not in_body or not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                w = parts[0].strip()
                py = parts[1].strip().replace(" ", "'")
                wt = int(parts[2]) if len(parts) >= 3 and parts[2].strip().isdigit() else 1
                if not all(ord(c) < 0x10000 for c in w):
                    continue
                if wt >= 5500:
                    entries[py][w] = max(entries[py].get(w, 0), wt)

    total_words = 0
    lines_out = []
    for py, wdict in sorted(entries.items()):
        # Sort words for this pinyin by descending weight, keep top 14 per exact syllable sequence
        sorted_words = sorted(wdict.items(), key=lambda x: (-x[1], x[0]))[:14]
        if not sorted_words:
            continue
        top_wt = sorted_words[0][1]
        words_str = ",".join(w for w, _ in sorted_words)
        total_words += len(sorted_words)
        lines_out.append(f"{py}\t{top_wt}\t{words_str}")

    with open(OUT_DICT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out) + "\n")

    print(f"Wrote {OUT_DICT}: {len(lines_out)} unique Pinyin keys, {total_words} total words, {os.path.getsize(OUT_DICT)} bytes")
    for test_py in ["ti'sheng", "ti'yan", "ti'sheng'ti'yan", "wei'ruan", "shuang'pin", "ping'ban", "jian'pan"]:
        wdict = entries.get(test_py, {})
        top = sorted(wdict.items(), key=lambda x: -x[1])[:6]
        print(f"  {test_py} -> {top}")

if __name__ == "__main__":
    main()
