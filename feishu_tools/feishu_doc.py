"""Feishu document read/write tools with token caching."""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

FEISHU_AUTH_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)
FEISHU_DOC_URL = "https://open.feishu.cn/open-apis/docx/v1/documents"
FEISHU_DRIVE_URL = "https://open.feishu.cn/open-apis/drive/v1/files"

# Token cache: {app_id:app_secret: {token, expires_at}}
_token_cache: dict[str, dict] = {}


def get_tenant_token(app_id: str, app_secret: str) -> str:
    cache_key = f"{app_id}:{app_secret}"
    cached = _token_cache.get(cache_key)
    if cached and cached["expires_at"] > time.time():
        return cached["token"]

    with httpx.Client(timeout=10) as client:
        resp = client.post(FEISHU_AUTH_URL, json={
            "app_id": app_id,
            "app_secret": app_secret,
        })
        resp.raise_for_status()
        data = resp.json()
        token = data["tenant_access_token"]
        _token_cache[cache_key] = {
            "token": token,
            "expires_at": time.time() + data.get("expire", 7200) - 300,
        }
        return token


def create_doc(title: str, folder_token: str, app_id: str,
               app_secret: str) -> dict:
    """Create a Feishu document, return {doc_id, doc_url}."""
    token = get_tenant_token(app_id, app_secret)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body: dict = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token
    with httpx.Client(timeout=15) as client:
        resp = client.post(FEISHU_DOC_URL, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        doc = data["data"]["document"]
        doc_id = doc["document_id"]
        doc_url = doc.get("url") or f"https://bytedance.feishu.cn/docx/{doc_id}"
        return {
            "doc_id": doc_id,
            "doc_url": doc_url,
        }


def delete_doc(doc_id: str, app_id: str, app_secret: str) -> bool:
    """Delete a feishu document via Drive API."""
    token = get_tenant_token(app_id, app_secret)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.delete(
                f"{FEISHU_DRIVE_URL}/{doc_id}?type=docx",
                headers=headers,
            )
            return resp.status_code == 200
    except Exception:
        return False


def write_doc_content(doc_id: str, content: str, app_id: str,
                      app_secret: str, folder_token: str = "") -> str:
    """Replace document content: delete old doc, create new one, return new doc_id.

    Feishu block-level editing is unreliable. Rebuilding the doc is simpler.
    """
    # Delete old doc
    if not delete_doc(doc_id, app_id, app_secret):
        logger.warning(f"Failed to delete old doc {doc_id}, proceeding anyway")

    # Create new doc (title = first line of content)
    first_line = content.split("\n")[0].strip()[:50] or "未命名"
    title = first_line.lstrip("#").strip()
    new_doc = create_doc(title, folder_token, app_id, app_secret)
    new_doc_id = new_doc["doc_id"]

    # Write content
    token = get_tenant_token(app_id, app_secret)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Get root block of new doc
    with httpx.Client(timeout=10) as client:
        resp = client.get(
            f"{FEISHU_DOC_URL}/{new_doc_id}/blocks",
            headers=headers,
        )
        resp.raise_for_status()
        root_block_id = resp.json()["data"]["items"][0]["block_id"]

    # Write paragraphs
    paragraphs = content.split("\n\n")
    blocks = []
    for p in paragraphs[:200]:
        p = p.strip()
        if not p:
            continue
        blocks.append({
            "block_type": 2,
            "text": {
                "elements": [{"text_run": {"content": p}}],
                "style": {},
            },
        })

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{FEISHU_DOC_URL}/{new_doc_id}/blocks/{root_block_id}/children",
            headers=headers,
            json={"children": blocks, "index": 0},
        )
        resp.raise_for_status()

    return new_doc_id


def read_doc_content(doc_id: str, app_id: str, app_secret: str) -> str:
    """Read raw text content from a Feishu document."""
    token = get_tenant_token(app_id, app_secret)
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(timeout=10) as client:
        resp = client.get(
            f"{FEISHU_DOC_URL}/{doc_id}/raw_content",
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("data", {}).get("content", "")
        # raw_content may return JSON with block structure
        if isinstance(content, str) and content.startswith("{"):
            try:
                parsed = json.loads(content)
                content = _extract_text_from_blocks(parsed)
            except (json.JSONDecodeError, KeyError):
                pass
        return content


def _extract_text_from_blocks(block_data: dict) -> str:
    """Recursively extract text from Feishu block structure."""
    texts: list[str] = []

    def walk(node: dict) -> None:
        if isinstance(node, dict):
            if "text" in node and isinstance(node["text"], dict):
                for elem in node["text"].get("elements", []):
                    if "text_run" in elem:
                        texts.append(elem["text_run"].get("content", ""))
            if "children" in node:
                for child in node["children"]:
                    walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(block_data)
    return "\n".join(texts)


# 9 fixed folders
FEISHU_FOLDERS = [
    "AI 技术理解", "产品方法论", "产品案例拆解",
    "行业与商业", "职业成长", "个人思考",
    "读书笔记", "健康与生活", "其他",
]

# Folder routing rules based on tag prefix matching
FOLDER_TAG_RULES: dict[str, list[str]] = {
    "AI 技术理解": ["ai-", "ml-", "deep-learning", "rag", "llm", "transformer"],
    "产品方法论": ["product-design", "pm-", "ux-", "交互", "用户研究"],
    "产品案例拆解": ["case-study", "product-analysis", "项目复盘"],
    "行业与商业": ["business", "industry", "competitive", "market"],
    "职业成长": ["career", "interview", "learning", "工作经验"],
    "个人思考": ["personal-", "decision", "感悟", "思考"],
    "读书笔记": ["reading-", "book-", "书评", "读书"],
    "健康与生活": ["health-", "sleep", "exercise", "mood", "日常"],
}


def pick_folder(tags: list[str]) -> str:
    """Route a capture to a folder based on tag keyword matching."""
    scores = {folder: 0 for folder in FEISHU_FOLDERS}
    for folder, keywords in FOLDER_TAG_RULES.items():
        for tag in tags:
            for kw in keywords:
                if kw.lower() in tag.lower():
                    scores[folder] += 1

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "其他"
