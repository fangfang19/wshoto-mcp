#!/usr/bin/env node

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";

// ── Path setup ──────────────────────────────────────────────
const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const DATA_DIR = join(ROOT, "data");
const BASE_URL = "https://help.wshoto.com";

const PORT = parseInt(process.env.PORT || "3030", 10);

// ── Load knowledge index ────────────────────────────────────
function loadKnowledge() {
  const path = join(DATA_DIR, "knowledge.json");
  if (!existsSync(path)) {
    throw new Error("knowledge.json not found. Run 'npm run build-index' first.");
  }
  return JSON.parse(readFileSync(path, "utf-8"));
}

// ── Search implementation ───────────────────────────────────
function search(query, knowledge) {
  const tokens = extractTokens(query);
  if (tokens.length === 0) return [];

  const keywordIndex = knowledge.keyword_index || {};
  const pages = knowledge.pages || [];
  const scores = new Map();

  for (const token of tokens) {
    const slugs = keywordIndex[token];
    if (!slugs) continue;
    for (const slug of slugs) {
      scores.set(slug, (scores.get(slug) || 0) + 1);
    }
  }

  // Fuzzy match
  const indexKeys = Object.keys(keywordIndex);
  for (const token of tokens) {
    for (const key of indexKeys) {
      if (key.includes(token) && key !== token) {
        const slugs = keywordIndex[key];
        for (const slug of slugs) {
          scores.set(slug, (scores.get(slug) || 0) + 0.5);
        }
      }
    }
  }

  const pageMap = new Map();
  for (const p of pages) pageMap.set(p.slug, p);

  return [...scores.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([slug, score]) => {
      const page = pageMap.get(slug);
      if (!page) return null;
      return {
        slug: page.slug,
        title: page.title,
        category: page.category,
        summary: page.summary,
        sections: (page.sections || []).map((s) => s.title).slice(0, 5),
        faq: (page.faq || []).map((f) => f.q).slice(0, 3),
        score: Math.round(score * 10) / 10,
      };
    })
    .filter(Boolean);
}

function extractTokens(query) {
  const tokens = new Set();
  const parts = query.split(/[\s,，。！？、：；""''（）\(\)\[\]【】]+/);
  for (const p of parts) {
    if (!p) continue;
    const t = p.toLowerCase();
    if (t.length <= 1 && !/[a-z]/i.test(t)) continue;
    tokens.add(t);
    if (/[\u4e00-\u9fa5]/.test(t) && t.length >= 2) {
      for (let len = 2; len <= Math.min(4, t.length); len++) {
        for (let i = 0; i <= t.length - len; i++) {
          tokens.add(t.slice(i, i + len));
        }
      }
    }
  }
  const engMatch = query.match(/[a-zA-Z]+/g);
  if (engMatch) {
    for (const w of engMatch) {
      const t = w.toLowerCase();
      if (t.length >= 2) tokens.add(t);
    }
  }
  return [...tokens];
}

// ── Page fetching ───────────────────────────────────────────
async function fetchPage(slug, knowledge) {
  const pages = knowledge.pages || [];
  const page = pages.find((p) => p.slug === slug);
  if (!page) return { error: `Page not found for slug: ${slug}` };

  const url = `https://help.wshoto.com/${slug}/README.md`;
  try {
    const resp = await fetch(url, {
      headers: { "User-Agent": "wshoto-mcp/1.0" },
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) {
      return {
        error: `Failed to fetch page (HTTP ${resp.status})`,
        url,
        local_summary: page.summary,
        local_sections: (page.sections || []).map((s) => s.title),
        local_faq: page.faq || [],
      };
    }
    const content = await resp.text();
    const images = [];
    const mdImgRegex = /!\[([^\]]*)\]\(([^)]+)\)/g;
    let m;
    while ((m = mdImgRegex.exec(content)) !== null) images.push({ alt: m[1], url: m[2] });
    const htmlImgRegex = /<img\s+[^>]*src="([^"]+)"[^>]*\/?>/gi;
    while ((m = htmlImgRegex.exec(content)) !== null) images.push({ alt: "", url: m[1] });
    const cleanContent = content
      .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, "[图片: $2]")
      .replace(/<img\s+[^>]*src="([^"]+)"[^>]*\/?>/gi, "[图片: $1]");
    return { slug: page.slug, title: page.title, category: page.category, content: cleanContent, images, image_count: images.length, url };
  } catch (err) {
    return { error: `Network error: ${err.message}`, url, local_summary: page.summary, local_sections: (page.sections || []).map((s) => s.title), local_faq: page.faq || [] };
  }
}

// ── Freshness check ─────────────────────────────────────────
async function checkFreshness(knowledge) {
  const localCount = (knowledge.meta || {}).total_pages || 0;
  try {
    const resp = await fetch(`${BASE_URL}/_sidebar.md`, {
      headers: { "User-Agent": "wshoto-mcp/1.0" },
      signal: AbortSignal.timeout(15000),
    });
    if (!resp.ok) return { fresh: null, error: `Failed to fetch sidebar (HTTP ${resp.status})`, local_count: localCount, generated_at: (knowledge.meta || {}).generated_at || "unknown" };
    const sidebar = await resp.text();
    const livePages = parseSidebar(sidebar);
    const liveCount = livePages.length;
    return {
      fresh: liveCount === localCount,
      live_count: liveCount,
      local_count: localCount,
      stale_by: liveCount - localCount,
      generated_at: (knowledge.meta || {}).generated_at || "unknown",
      needs_rebuild: liveCount !== localCount,
      rebuild_command: liveCount !== localCount ? "Run 'python3 scripts/build_index.py' from package root, then restart this MCP server." : null,
    };
  } catch (err) {
    return { fresh: null, error: `Failed to check: ${err.message}`, local_count: localCount, generated_at: (knowledge.meta || {}).generated_at || "unknown" };
  }
}

function parseSidebar(content) {
  const pages = [];
  let currentCategory = "";
  for (const rawLine of content.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("<!--")) continue;
    const subMatch = rawLine.match(/^\s+[-*]\s+\[(.+?)\]\(([^)]+)\)/);
    if (subMatch && currentCategory) {
      const title = subMatch[1].trim();
      const slug = extractSidebarSlug(subMatch[2].trim());
      if (slug && title && !title.includes("$$hide$$")) pages.push({ slug, title, category: currentCategory });
      continue;
    }
    const catMatch = line.match(/^[-*]\s+\*?\*?(.+?)\*?\*?\s*$/);
    if (catMatch && !line.includes("[")) {
      let catRaw = catMatch[1].trim().replace(/\([^)]*\)/g, "").trim();
      currentCategory = catRaw.includes("$$hide$$") ? "" : catRaw;
      continue;
    }
    const catLinkMatch = line.match(/^[-*]\s+\[(.+?)\]\(([^)]+)\)\s*$/);
    if (catLinkMatch) {
      currentCategory = catLinkMatch[1].trim();
      const slug = extractSidebarSlug(catLinkMatch[2]);
      const title = catLinkMatch[1].trim();
      if (slug && title) pages.push({ slug, title, category: "入门" });
    }
  }
  return pages;
}

function extractSidebarSlug(href) {
  if (!href) return null;
  let slug = href.replace(/^\/+/, "").replace(/\/README$/i, "");
  return slug || null;
}

// ── Categories ───────────────────────────────────────────────
function getCategories(knowledge) {
  const pages = knowledge.pages || [];
  const catMap = new Map();
  for (const p of pages) {
    const cat = p.category || "未分类";
    if (!catMap.has(cat)) catMap.set(cat, { category: cat, count: 0, pages: [] });
    const entry = catMap.get(cat);
    entry.count++;
    entry.pages.push({ slug: p.slug, title: p.title });
  }
  return { total_categories: catMap.size, total_pages: pages.length, categories: [...catMap.values()] };
}

// ── Create MCP Server ────────────────────────────────────────
function createMcpServer() {
  const server = new Server(
    { name: "wshoto-mcp", version: "1.0.0" },
    { capabilities: { tools: {} } }
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "wshoto_search",
        description: "搜索微盛企微管家帮助文档。输入中文关键词（如「员工活码」「裂变」「AI短剧」），返回匹配的文档页面列表，包含摘要、章节和FAQ。",
        inputSchema: {
          type: "object",
          properties: { query: { type: "string", description: "搜索关键词，支持中文和英文" } },
          required: ["query"],
        },
      },
      {
        name: "wshoto_get_page",
        description: "获取指定帮助页面的完整内容（含操作截图URL）。传入页面 slug（从 wshoto_search 结果中获取）。",
        inputSchema: {
          type: "object",
          properties: { slug: { type: "string", description: "页面标识符，从 wshoto_search 结果的 slug 字段获取" } },
          required: ["slug"],
        },
      },
      {
        name: "wshoto_refresh",
        description: "检查本地文档索引是否与官网同步。对比线上页数与本地索引，如不一致提示是否需要重建。",
        inputSchema: { type: "object", properties: {}, required: [] },
      },
      {
        name: "wshoto_categories",
        description: "列出微盛企微管家帮助文档的全部产品分类及每类包含的页面数量。",
        inputSchema: { type: "object", properties: {}, required: [] },
      },
    ],
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    try {
      const knowledge = loadKnowledge();
      switch (name) {
        case "wshoto_search": {
          const query = args?.query || "";
          if (!query.trim()) return { content: [{ type: "text", text: JSON.stringify({ error: "请输入搜索关键词" }, null, 2) }] };
          const results = search(query, knowledge);
          return { content: [{ type: "text", text: JSON.stringify({ query, total: results.length, results }, null, 2) }] };
        }
        case "wshoto_get_page": {
          const slug = args?.slug || "";
          if (!slug.trim()) return { content: [{ type: "text", text: JSON.stringify({ error: "请提供页面 slug" }, null, 2) }] };
          const data = await fetchPage(slug, knowledge);
          return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
        }
        case "wshoto_refresh": {
          const status = await checkFreshness(knowledge);
          return { content: [{ type: "text", text: JSON.stringify(status, null, 2) }] };
        }
        case "wshoto_categories": {
          const data = getCategories(knowledge);
          return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
        }
        default:
          return { content: [{ type: "text", text: JSON.stringify({ error: `Unknown tool: ${name}` }) }] };
      }
    } catch (err) {
      return { content: [{ type: "text", text: JSON.stringify({ error: err.message, hint: "Try running 'npm run build-index' first." }, null, 2) }] };
    }
  });

  return server;
}

// ── Start HTTP server with Streamable HTTP transport ─────────
async function main() {
  const mcpServer = createMcpServer();
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined, // stateless mode — simpler for URL-based MCP
  });

  // Connect server to transport
  await mcpServer.connect(transport);

  const httpServer = createServer(async (req, res) => {
    // CORS
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Mcp-Session-Id");

    if (req.method === "OPTIONS") {
      res.writeHead(204);
      res.end();
      return;
    }

    // Health check
    if (req.method === "GET" && req.url === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok", name: "wshoto-mcp", version: "1.0.0", mode: "sse" }));
      return;
    }

    // MCP endpoint
    if (req.url === "/mcp" || req.url === "/sse") {
      // Collect body for POST
      let body = undefined;
      if (req.method === "POST") {
        const chunks = [];
        for await (const chunk of req) chunks.push(chunk);
        const raw = Buffer.concat(chunks).toString();
        try { body = JSON.parse(raw); } catch { body = raw; }
      }
      await transport.handleRequest(req, res, body);
      return;
    }

    // 404
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Not found. Use /mcp for MCP, /health for status." }));
  });

  httpServer.listen(PORT, "0.0.0.0", () => {
    console.error(`✓ wshoto-mcp SSE server running on http://localhost:${PORT}/mcp`);
    console.error(`  Health check: http://localhost:${PORT}/health`);
    console.error(`  Mode: Streamable HTTP (stateless)`);
  });
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
