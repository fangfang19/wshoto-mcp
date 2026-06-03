#!/usr/bin/env python3
"""
批量抓取微盛帮助文档所有页面，提取关键信息，生成知识索引。

数据流：_sidebar.md (线上唯一真相源) → 解析菜单树 → 抓取所有页面 → knowledge.json
同时自动更新 references/pages.md 保持本地目录树同步。

用法: python build_index.py
输出: references/knowledge.json + 更新 references/pages.md
"""
import json
import re
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

BASE_URL = "https://help.wshoto.com"
SKILL_DIR = os.path.dirname(os.path.dirname(__file__))
OUTPUT = os.path.join(SKILL_DIR, "references", "knowledge.json")
PAGES_MD = os.path.join(SKILL_DIR, "references", "pages.md")
MAX_WORKERS = 15
TIMEOUT = 15

def fetch_sidebar():
    """从线上拉取 _sidebar.md，解析出完整菜单树（分类→页面列表）"""
    url = f"{BASE_URL}/_sidebar.md"
    try:
        req = Request(url, headers={"User-Agent": "Wshoto-Knowledge-Builder/1.0"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError) as e:
        print(f"❌ 无法拉取 _sidebar.md: {e}", file=sys.stderr)
        sys.exit(1)

    # 解析 markdown 层级结构
    # docsify sidebar 格式:
    # * 获客拉新(huokelaxin)           ← 分类行（顶级，无缩进）
    #     * [员工活码](yghm)            ← 子页面（4空格缩进）
    #     * [裂变活动$$hide$$](xxx)     ← 隐藏页面（跳过）
    pages = []
    current_category = ""

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue

        # ⚠️ 子页面匹配必须用原始 line（缩进是区分层级的依据）
        sub_match = re.match(r'^\s+[-*]\s+\[(.+?)\]\(([^)]+)\)', line)
        if sub_match and current_category:
            title = sub_match.group(1).strip()
            raw_href = sub_match.group(2).strip()
            slug = extract_slug(raw_href)
            if slug and title and "$$hide$$" not in title:
                pages.append({"slug": slug, "title": title, "category": current_category})
            continue

        # 顶级分类: * 获客拉新(huokelaxin) 或 * 常见问题$$hide$$
        cat_match = re.match(r'^[-*]\s+\*?\*?(.+?)\*?\*?\s*$', stripped)
        if cat_match and "[" not in stripped:
            cat_raw = cat_match.group(1).strip()
            cat_clean = re.sub(r'\([^)]*\)', '', cat_raw).strip()
            if "$$hide$$" in cat_clean:
                current_category = ""
            else:
                current_category = cat_clean
            continue

        # 顶级链接分类: * [帮助中心](/)
        cat_link_match = re.match(r'^[-*]\s+\[(.+?)\]\(([^)]+)\)\s*$', stripped)
        if cat_link_match:
            current_category = cat_link_match.group(1).strip()
            slug = extract_slug(cat_link_match.group(2))
            title = cat_link_match.group(1).strip()
            if slug and title:
                pages.append({"slug": slug, "title": title, "category": "入门"})
            continue

    # 去重（按 slug）
    seen = set()
    deduped = []
    for p in pages:
        if p["slug"] not in seen:
            seen.add(p["slug"])
            deduped.append(p)

    return deduped

def extract_slug(raw_href):
    """从 /slug 或 /slug/README 中提取纯 slug"""
    href = raw_href.strip().rstrip("/")
    if href == "/" or href == "":
        return "/"
    # 去掉 /README 后缀
    href = re.sub(r'/README$', '', href)
    href = re.sub(r'\.md$', '', href)
    # 去掉前导 /
    href = href.lstrip("/")
    return href if href else "/"

def write_pages_md(pages):
    """将解析的菜单树写入 pages.md（方便人类阅读）"""
    import collections
    # 按分类分组
    cats = collections.OrderedDict()
    for p in pages:
        cat = p.get("category", "其他")
        if cat not in cats:
            cats[cat] = []
        cats[cat].append(p)

    lines = [
        "# 微盛企微管家帮助文档 - 完整目录",
        "",
        f"> 自动生成于 {time.strftime('%Y-%m-%d %H:%M:%S')}，数据来源: {BASE_URL}/_sidebar.md",
        "",
        "| Slug | 标题 | 分类 |",
        "|------|------|------|",
    ]

    for cat, items in cats.items():
        lines.append(f"| | **{cat}** | |")
        for p in items:
            lines.append(f"| {p['slug']} | {p['title']} | {p['category']} |")

    with open(PAGES_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"📄 已更新 {PAGES_MD} ({len(pages)} 个页面)")

def fetch_page(slug):
    """抓取单个页面并提取关键信息"""
    # 首页特殊处理
    if slug == "/":
        url = f"{BASE_URL}/README.md"
    else:
        url = f"{BASE_URL}/{slug}/README.md"

    try:
        req = Request(url, headers={"User-Agent": "Wshoto-Knowledge-Builder/1.0"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError) as e:
        print(f"  ⚠ 抓取失败: {slug} ({e})", file=sys.stderr)
        return None

    return extract_info(slug, content)

def extract_info(slug, content):
    """从 Markdown 内容中提取结构化信息"""
    info = {"slug": slug, "sections": [], "keywords": []}

    lines = content.split("\n")

    # 提取标题 (第一个 H1)
    for line in lines:
        h1_match = re.match(r'^#\s+(.+?)(?:\s*\{.*\})?\s*$', line)
        if h1_match:
            info["title"] = h1_match.group(1).strip()
            break
    if "title" not in info:
        info["title"] = slug

    # 提取所有章节标题 (H2, H3)
    for line in lines:
        h_match = re.match(r'^(#{2,3})\s+(.+?)(?:\s*\{.*\})?\s*$', line)
        if h_match:
            level = len(h_match.group(1))
            title = h_match.group(2).strip()
            # 跳过表格中的 # 误匹配
            if not re.match(r'^[-\s|]+$', title) and "---" not in title:
                info["sections"].append({"level": level, "title": title})

    # 提取摘要：标题后的前几段非空文本（跳过图片、表格、列表）
    summary_lines = []
    in_first_h1 = False
    for line in lines:
        stripped = line.strip()
        if re.match(r'^#\s+', stripped):
            if not in_first_h1:
                in_first_h1 = True
                continue
            else:
                break
        if in_first_h1:
            if stripped and not re.match(r'^[#|!\[`\-*>]', stripped) and not re.match(r'^<', stripped):
                # 清理 HTML 标签和 Markdown 链接语法
                clean = re.sub(r'<[^>]+>', '', stripped)
                clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)
                clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean)
                if len(clean) > 10:
                    summary_lines.append(clean)
                if len(" ".join(summary_lines)) > 400:
                    break
    info["summary"] = " ".join(summary_lines[:6])[:600] if summary_lines else ""

    # 提取 FAQ
    faqs = []
    in_faq = False
    current_q = None
    current_a = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^#+\s*(?:常见问题|FAQ|Q&A)', stripped, re.IGNORECASE):
            in_faq = True
            continue
        if in_faq and re.match(r'^#{2,}', stripped):
            break
        if in_faq:
            q_match = re.match(r'^(?:>?\s*)?\*\*(?:Q[:\s]*)?(.+?)(?:\*\*)?\s*$', stripped)
            if q_match:
                if current_q:
                    faqs.append({"q": current_q, "a": "\n".join(current_a)[:500]})
                current_q = q_match.group(1).strip()
                current_a = []
                continue
            if current_q and stripped:
                clean = re.sub(r'<[^>]+>', '', stripped)
                current_a.append(clean)
    if current_q:
        faqs.append({"q": current_q, "a": "\n".join(current_a)[:500]})
    if faqs:
        info["faq"] = faqs

    # 提取关键词：标题 + 章节标题分词
    all_text = " ".join([info.get("title", "")] + [s["title"] for s in info["sections"]])
    keywords = set()

    # 黑名单：太泛的词不纳入索引
    blacklist = {
        "常见问题", "使用场景", "操作步骤", "功能概述", "配置流程",
        "功能介绍", "快速了解", "使用指引", "注意事项", "如何配置",
        "什么是", "适用于", "解决方案", "温馨提示", "操作指南", "相关配置",
        "使用教程", "如何设置", "设置方法", "操作说明", "配置说明",
    }

    # 提取中文关键词（2-10字的连续中文片段，放宽上限以捕获长标题）
    cn_terms = re.findall(r'[\u4e00-\u9fff]{2,10}', all_text)
    for term in cn_terms:
        if term not in blacklist:
            keywords.add(term)
        # 关键改进：对每个长词做滑动窗口拆分子串
        # "好友裂变" → 加入 "好友"、"裂变"、"好友裂变"
        # "会话存档检索" → 加入 "会话存档"、"存档检索"
        # 这样用户搜"裂变""会话存档"都能命中
        if len(term) >= 3:
            for win_size in (2, 3, 4):
                for i in range(len(term) - win_size + 1):
                    sub = term[i:i + win_size]
                    if sub not in blacklist:
                        keywords.add(sub)

    # 额外：对标题本身做分隔符拆分（处理"小程序商城-秒杀"这种格式）
    title = info.get("title", "")
    for sep in ("-", "—", "·", "｜", "|", "/"):
        for part in title.split(sep):
            part = part.strip()
            if len(part) >= 2:
                keywords.add(part)
            # 对拆分后的部分也做子串提取
            cn_in_part = re.findall(r'[\u4e00-\u9fff]{2,6}', part)
            for t in cn_in_part:
                if t not in blacklist:
                    keywords.add(t)
                if len(t) >= 3:
                    for win_size in (2, 3, 4):
                        for i in range(len(t) - win_size + 1):
                            sub = t[i:i + win_size]
                            if sub not in blacklist:
                                keywords.add(sub)

    # 提取英文/数字关键词
    en_terms = re.findall(r'[A-Za-z0-9_]{2,}', all_text)
    for term in en_terms:
        if len(term) >= 2 and not term.isdigit():
            keywords.add(term.lower())

    info["keywords"] = sorted(list(keywords))[:50]  # 放宽限制，子串会多些

    return info

def _extract_title_keywords(title):
    """从侧边栏标题提取关键词（与 extract_info 内逻辑一致）"""
    kws = set()
    blacklist = {
        "常见问题", "使用场景", "操作步骤", "功能概述", "配置流程",
        "功能介绍", "快速了解", "使用指引", "注意事项", "如何配置",
        "什么是", "适用于", "解决方案", "温馨提示", "操作指南", "相关配置",
        "使用教程", "如何设置", "设置方法", "操作说明", "配置说明",
    }
    # 分隔符拆分
    for sep in ("-", "—", "·", "｜", "|", "/"):
        for part in title.split(sep):
            part = part.strip()
            if len(part) >= 2:
                kws.add(part)
            cn_in_part = re.findall(r'[\u4e00-\u9fff]{2,10}', part)
            for t in cn_in_part:
                if t not in blacklist:
                    kws.add(t)
                if len(t) >= 3:
                    for win_size in (2, 3, 4):
                        for i in range(len(t) - win_size + 1):
                            sub = t[i:i + win_size]
                            if sub not in blacklist:
                                kws.add(sub)
    # 英文关键词
    en_terms = re.findall(r'[A-Za-z0-9_]{2,}', title)
    for term in en_terms:
        if len(term) >= 2 and not term.isdigit():
            kws.add(term.lower())
    return kws

def build_keyword_index(pages_data):
    """构建关键词反查索引"""
    index = {}
    for page in pages_data:
        if not page:
            continue
        slug = page["slug"]
        terms = set(page.get("keywords", []))
        terms.add(page.get("title", ""))
        for term in terms:
            if len(term) < 2:
                continue
            term_lower = term.lower()
            if term_lower not in index:
                index[term_lower] = []
            if slug not in index[term_lower]:
                index[term_lower].append(slug)
    # 限制每个关键词最多关联25个页面（平衡覆盖面与索引大小）
    return {k: v[:25] for k, v in index.items()}

def main():
    start = time.time()

    # Step 1: 从线上拉取最新菜单（唯一真相源）
    print("🔍 从线上拉取最新菜单结构...")
    pages_meta = fetch_sidebar()
    print(f"📚 解析到 {len(pages_meta)} 个页面")

    # Step 2: 自动更新本地 pages.md
    write_pages_md(pages_meta)

    # Step 3: 批量抓取每个页面
    print(f"\n🚀 开始批量抓取 {len(pages_meta)} 个页面...\n")

    results = []
    failed = []
    done = 0
    total = len(pages_meta)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_page, p["slug"]): p for p in pages_meta}
        for future in as_completed(futures):
            page = futures[future]
            done += 1
            try:
                result = future.result()
                if result:
                    # 合并侧边栏元数据
                    sidebar_title = page["title"]
                    result["title"] = sidebar_title
                    result["category"] = page["category"]
                    # 关键：从侧边栏标题也提取关键词并合并（H1 和侧边栏标题可能不同）
                    extra_kw = _extract_title_keywords(sidebar_title)
                    existing_kw = set(result.get("keywords", []))
                    result["keywords"] = sorted(list(existing_kw | extra_kw))[:60]
                    results.append(result)
                else:
                    # 使用元数据作为兜底
                    results.append({
                        "slug": page["slug"],
                        "title": page["title"],
                        "category": page["category"],
                        "summary": "",
                        "sections": [],
                        "keywords": [],
                    })
                    failed.append(page["slug"])
            except Exception as e:
                print(f"  ❌ {page['slug']}: {e}", file=sys.stderr)
                failed.append(page["slug"])

            if done % 20 == 0 or done == total:
                print(f"  进度: {done}/{total}")

    # Step 4: 构建关键词索引
    keyword_index = build_keyword_index(results)

    knowledge = {
        "meta": {
            "source": "https://help.wshoto.com",
            "total_pages": len(results),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "version": "1.0",
        },
        "pages": results,
        "keyword_index": keyword_index,
    }

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(knowledge, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start

    # Compare with previous index to report actual changes
    old_total = len(pages_meta)  # default: no previous data
    try:
        with open(OUTPUT, "r", encoding="utf-8") as f:
            old_data = json.load(f)
            old_total = old_data["meta"]["total_pages"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    new_total = len(results)
    delta = new_total - old_total
    delta_str = f" (+{delta})" if delta > 0 else f" ({delta})" if delta < 0 else ""

    print(f"\n✅ 完成！{new_total} 个页面已索引，{len(keyword_index)} 个关键词{delta_str}")
    print(f"   用时 {elapsed:.1f}秒，失败 {len(failed)} 个: {failed[:5]}")
    print(f"   旧索引: {old_total} 页 → 新索引: {new_total} 页")
    print(f"   输出: {OUTPUT}")

if __name__ == "__main__":
    main()
