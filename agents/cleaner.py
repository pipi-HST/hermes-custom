"""Content cleaning pipeline — strip ads, navigation, filler words."""
from __future__ import annotations

import re

# 广告/导航关键词
AD_PATTERNS = [
    r"关注我们[,，]?获取更多精彩内容",
    r"长按识别二维码",
    r"扫描二维码",
    r"推荐阅读[：:]",
    r"你可能还喜欢[：:]",
    r"广告[：:]",
    r"推广[：:]",
    r"点击上方蓝字关注",
    r"设为星标",
    r"<end>",
    r"---\s*$",
]

# 语音填充词
FILLER_PATTERNS = [
    r"嗯那个",
    r"嗯就是",
    r"就是那个",
    r"就是说",
    r"然后呢",
    r"那个那个",
]

# HTML 标签
HTML_TAG_RE = re.compile(r"<[^>]*>")

# 多余空行
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def clean_text(raw: str) -> str:
    if not raw or not raw.strip():
        return ""

    text = raw.strip()

    # 去 HTML 标签
    text = HTML_TAG_RE.sub("", text)

    # 去广告/导航行
    for pattern in AD_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    # 去语音填充词（中文）
    for pattern in FILLER_PATTERNS:
        text = re.sub(pattern, "", text)

    # 去多余空行
    text = MULTI_NEWLINE_RE.sub("\n\n", text)

    # 去首尾空白
    text = text.strip()

    # 确保单行没有多余空格
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    return text


def format_for_display(text: str) -> str:
    """将清洗后的文本格式化为可读的飞书文档内容。

    规则：
    - 按空行自然分段
    - 合并太短的相邻段（<50字符的合并到前段）
    - 去掉纯标点/空白的无效段
    - 每段间保留一个空行
    """
    if not text.strip():
        return text

    paragraphs = text.split("\n\n")
    result = []
    for p in paragraphs:
        p = p.strip()
        # 跳过纯空白或纯标点的段落
        if not p or all(c in "，。！？；：、""''（）…—・" for c in p):
            continue
        # 如果段落太短，和前一段合并
        if result and len(p) < 50:
            result[-1] = result[-1] + "\n" + p
        else:
            result.append(p)

    return "\n\n".join(result)


def is_meaningful_content(text: str) -> bool:
    """判断文本是否像真正的文章正文。"""
    if not text or not text.strip():
        return False
    t = text.strip()

    failure_markers = (
        "[内容未能提取]",
        "[小红书内容未能提取]",
        "请复制粘贴正文给我",
        "内容提取失败",
    )
    if any(marker in t for marker in failure_markers):
        return False

    # 中文正文：至少有一句完整中文句子，且中文字符数量足够。
    has_chinese_sentence = bool(re.search(r"[一-鿿].*[。！？]", t))
    chinese_chars = len(re.findall(r'[一-鿿]', t))
    if has_chinese_sentence and chinese_chars >= 50:
        return True

    # 英文/外文正文：不依赖中文字符，使用句子数量和词数避免误收短导航。
    latin_words = re.findall(r"[A-Za-z][A-Za-z'-]*", t)
    latin_sentences = re.findall(r"[A-Za-z][^.!?]{20,}[.!?]", t)
    return len(latin_words) >= 40 and len(latin_sentences) >= 2
