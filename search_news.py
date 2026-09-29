# -*- coding: utf-8 -*-
"""
Tavily 新闻搜索服务模块

职责：
1. 使用 Tavily API 搜索股票相关新闻
2. 支持多 Key 负载均衡
3. 搜索结果格式化
"""

import logging
import os
from copy import deepcopy
from email.utils import parsedate_to_datetime
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── 已知的页面无正文内容的 URL 模式 ─────────────────────────
_PORTAL_PAGE_PATTERNS = [
    # 股票行情/数据概览页（无文章正文）
    r"vip\.stock\.finance\.sina\.com\.cn",
    r"money\.finance\.sina\.com\.cn/corp",  # 新浪股票公告/年报页面
    r"q\.stock\.sohu\.com",
    r"data\.eastmoney\.com/stockdata",
    r"data\.eastmoney\.com/stock",
    r"quote\.cfi\.cn",
    r"stockpage\.10jqka\.com\.cn",
    r"gu\.qq\.com",
    r"aastocks\.com",
    r"quote\.eastmoney\.com",
    r"guba\.eastmoney\.com",  # 股吧论坛（非新闻）
    r"basic\.10jqka\.com\.cn",  # 同花顺公司资料页
    r"stock\.10jqka\.com\.cn",
    r"m\.lejucaijing\.com",  # 列表/搜索页面
    r"baike\.baidu\.com",  # 百科条目（非新闻）
    r"hk\.finance\.yahoo\.com",  # JS 渲染，无法提取正文
    r"xueqiu\.com",  # 雪球股票页面（非新闻）
]

# ── 正文抓取（trafilatura → newspaper3k → requests）───


def _is_portal_page(url: str) -> bool:
    """判断 URL 是否为股票信息门户页（非新闻文章）"""
    return any(re.search(p, url) for p in _PORTAL_PAGE_PATTERNS)


def _fetch_content(url: str, timeout: int = 8) -> str:
    """获取网页正文内容

    优先级：trafilatura → newspaper3k → requests + 正则
    """
    # ── 1. trafilatura（最佳，专为正文提取设计）───
    try:
        import trafilatura as _traf

        _downloaded = _traf.fetch_url(url, timeout=timeout)
        if _downloaded:
            _text = _traf.extract(
                _downloaded,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )
            if _text and len(_text.strip()) > 80:
                lines = [l.strip() for l in _text.strip().split("\n") if l.strip()]
                return "\n".join(lines)[:800]
    except Exception as e:
        logger.debug(f"trafilatura 失败 {url}: {e}")

    # ── 2. newspaper3k（备选）───
    try:
        from newspaper import Article, Config

        cfg = Config()
        cfg.browser_user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        cfg.request_timeout = timeout
        cfg.fetch_images = False
        cfg.memoize_articles = False

        article = Article(url, config=cfg, language="zh")
        article.download()
        article.parse()
        text = article.text.strip()
        if text and len(text) > 80:
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            return "\n".join(lines)[:800]
    except Exception as e:
        logger.debug(f"newspaper3k 失败 {url}: {e}")

    # ── 3. requests + 标签剥离 + 正则提取（最后手段）───
    try:
        import requests as _req

        _s = _req.Session()
        _s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
            }
        )
        resp = _s.get(url, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text

        # 剥离 script / style
        html = re.sub(
            r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE
        )
        # 取 body
        body = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL)
        text = body.group(1) if body else html
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text).strip()

        # 提取有用文本（中文占比 > 30% 或含日期/金额的行）
        lines = [l.strip() for l in text.replace("\r", "").split("\n") if l.strip()]
        useful = []
        for l in lines:
            chinese = len(re.findall(r"[一-鿿]", l))
            has_date = bool(re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", l))
            has_money = bool(re.search(r"[万亿千]\b|\.\d{2}%|\d+\.?\d*亿元|\d+\.?\d*万元", l))
            # 跳过纯导航行
            if re.match(
                r"^(首页|财经|股票|基金|行情|数据|股吧|博客|登录|注册|设为首页|加入收藏|导航|热点|工具|返回)",
                l,
            ):
                continue
            if len(l) < 8:
                continue
            if chinese > 3 or has_date or has_money:
                useful.append(l)

        result = "\n".join(useful)[:600]
        if result and len(result) > 80:
            return result
    except Exception as e:
        logger.debug(f"requests 抓取失败 {url}: {e}")

    return ""


# ── 噪声检测与摘要修复 ──────────────────────────────────


def _is_noisy_snippet(text: str) -> bool:
    """判断摘要是否主要是导航模板噪声"""
    if not text or len(text.strip()) < 30:
        return True
    noise_count = sum(
        1
        for p in [r"@\w+@", r"<!---", r"___.*?___", r"加载中", r"自选股", r"最近访问", r"热点栏目"]
        if re.search(p, text)
    )
    chinese_chars = len(re.findall(r"[一-鿿]", text))
    total_chars = len(text.strip())
    chinese_ratio = chinese_chars / total_chars if total_chars > 0 else 0
    return noise_count >= 2 or chinese_ratio < 0.1


# ── 数据类 ──────────────────────────────────────────────


@dataclass
class SearchResult:
    """搜索结果数据类"""

    title: str
    snippet: str
    url: str
    source: str
    published_date: Optional[str] = None
    raw_content: str = ""

    def to_text(self) -> str:
        date_str = f" ({self.published_date})" if self.published_date else " [发布日期待核验]"
        # 优先用 raw_content，其次是 snippet
        body = self.snippet[:500]
        return f"【{self.source}】{self.title}{date_str}\n{body}\n来源：{self.url}"


@dataclass
class SearchResponse:
    """搜索响应"""

    query: str
    results: List[SearchResult]
    provider: str
    success: bool = True
    error_message: Optional[str] = None
    search_time: float = 0.0

    def to_context(self, max_results: int = 5) -> str:
        if not self.success or not self.results:
            return f"搜索 '{self.query}' 未找到相关结果。原因：{self.error_message or '无匹配结果'}"
        lines = [f"【{self.query} 搜索结果】（来源：{self.provider}）"]
        for i, result in enumerate(self.results[:max_results], 1):
            lines.append(f"\n{i}. {result.to_text()}")
        return "\n".join(lines)


# ── Tavily 搜索 ─────────────────────────────────────────


class TavilySearchProvider:
    """Tavily 搜索引擎"""

    def __init__(self, api_keys: List[str]):
        self._api_keys = [key.strip() for key in api_keys if key and key.strip()]
        self._name = "Tavily"
        self._key_index = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_available(self) -> bool:
        return bool(self._api_keys)

    def _get_next_key(self) -> Optional[str]:
        if not self._api_keys:
            return None
        key = self._api_keys[self._key_index % len(self._api_keys)]
        self._key_index += 1
        return key

    def search(
        self,
        query: str,
        max_results: int = 5,
        days: int = 7,
        topic: Optional[str] = None,
    ) -> SearchResponse:
        api_key = self._get_next_key()
        if not api_key:
            return SearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message="Tavily 未配置 API Key",
            )

        try:
            from tavily import TavilyClient
        except ImportError:
            return SearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message="tavily-python 未安装，请运行: pip install tavily-python",
            )

        start = time.time()
        try:
            client = TavilyClient(api_key=api_key)
            params: Dict[str, Any] = {
                "query": query,
                "search_depth": "advanced",
                "max_results": max_results,
                "include_answer": False,
                # 获取原始正文（PDF 研报、部分新闻网站可返回真实内容）
                "include_raw_content": True,
                "timeout": 20,
                "topic": topic or "news",
            }
            if params["topic"] == "news":
                params["days"] = days
            else:
                params["start_date"] = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

            resp = client.search(**params)
            results = []
            for item in resp.get("results", []):
                title = item.get("title", "")
                snippet = (item.get("content") or "")[:500]
                url = item.get("url") or ""
                if urlparse(url).scheme not in {"http", "https"}:
                    continue
                raw_content = item.get("raw_content", "") or ""

                # 过滤已知的股票概览门户页（这些页面只有导航，没有文章正文）
                if _is_portal_page(url):
                    logger.debug(f"跳过门户页: {url[:60]}")
                    continue

                # 如果 raw_content 有意义，用它替代 snippet
                clean_raw = _extract_meaningful_text(raw_content) if raw_content else ""
                if clean_raw and len(clean_raw) > 100:
                    snippet = clean_raw[:500]

                results.append(
                    SearchResult(
                        title=title,
                        snippet=snippet,
                        url=url,
                        source=self._extract_domain(url),
                        published_date=item.get("published_date")
                        or item.get("publishedDate"),
                        raw_content=snippet if clean_raw else "",
                    )
                )

            elapsed = time.time() - start
            logger.info(
                f"[Tavily] 搜索 '{query}' 成功，{len(results)} 条，耗时 {elapsed:.2f}s"
            )
            return SearchResponse(
                query=query,
                results=results,
                provider=self._name,
                success=True,
                search_time=elapsed,
            )

        except Exception as e:
            err = str(e)
            for secret in self._api_keys:
                err = err.replace(secret, "[REDACTED]")
            if "rate limit" in err.lower() or "quota" in err.lower():
                err = f"API 配额已用尽: {err}"
            elapsed = time.time() - start
            logger.warning(f"[Tavily] 搜索 '{query}' 失败: {err}")
            return SearchResponse(
                query=query,
                results=[],
                provider=self._name,
                success=False,
                error_message=err,
                search_time=elapsed,
            )

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            return urlparse(url).netloc.replace("www.", "") or "未知来源"
        except Exception:
            return "未知来源"


# ── SearchService ────────────────────────────────────────


class SearchService:
    """新闻搜索服务"""

    _CHINESE_RE = re.compile(r"[㐀-䶿一-鿿]")
    _US_STOCK_RE = re.compile(r"^[A-Za-z]{1,5}(\.[A-Za-z])?$")

    def __init__(self, tavily_keys: Optional[List[str]] = None, news_max_age_days: int = 7):
        self._tavily = TavilySearchProvider(tavily_keys) if tavily_keys else None
        self.news_max_age_days = max(1, news_max_age_days)
        self._cache: Dict[str, Tuple[float, SearchResponse]] = {}
        self._cache_ttl = 600

    @property
    def is_available(self) -> bool:
        return self._tavily is not None and self._tavily.is_available

    def _cache_key(self, query: str, max_results: int, days: int) -> str:
        return f"{query}|{max_results}|{days}"

    def _get_cached(self, key: str) -> Optional[SearchResponse]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, resp = entry
        if time.time() - ts > self._cache_ttl:
            self._cache.pop(key, None)
            return None
        return deepcopy(resp)

    def _put_cache(self, key: str, response: SearchResponse) -> None:
        self._cache[key] = (time.time(), deepcopy(response))

    def search_news(
        self,
        query: str,
        max_results: int = 5,
        days: int = 7,
        topic: Optional[str] = None,
    ) -> SearchResponse:
        """通用新闻搜索；无配置返回显式失败，不抛 AttributeError。"""
        if not query.strip() or not 1 <= max_results <= 20 or days < 1:
            raise ValueError("query不能为空，max_results须为1..20，days须为正数")
        if not self.is_available:
            return SearchResponse(query, [], "Tavily", success=False, error_message="未配置 TAVILY_API_KEY")
        cache_key = self._cache_key(query, max_results, days)
        if topic:
            cache_key += f"|topic={topic}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        response = self._tavily.search(
            query, max_results=max_results, days=days, topic=topic
        )
        if response.success:
            self._put_cache(cache_key, response)
        return response

    def search_stock_news(
        self,
        stock_code: str,
        stock_name: str,
        max_results: int = 5,
        days: Optional[int] = None,
    ) -> SearchResponse:
        """搜索股票新闻

        使用多个查询来提升召回率，去重合并。
        对低质量摘要尝试网页正文提取（_fetch_content）。
        """
        if not 1 <= max_results <= 20 or (days is not None and days < 1):
            raise ValueError("max_results须为1..20，days须为正数")
        started = time.monotonic()
        errors = []
        is_foreign = self._is_foreign_stock(stock_code)
        prefer_chinese = not is_foreign and self._has_chinese(stock_name)

        if prefer_chinese:
            # 去掉"公告"（会返回股票公告列表页而非新闻文章）
            queries = [
                f"{stock_name} {stock_code} 最新消息",
                f"{stock_name} {stock_code} 资讯",
                f"{stock_name} {stock_code}",
            ]
        elif is_foreign:
            queries = [f"{stock_name} {stock_code} stock news"]
        else:
            queries = [f"{stock_name} {stock_code} news"]

        search_days = days if days is not None else self.news_max_age_days
        all_results: List[SearchResult] = []
        seen_urls: set = set()

        for q in queries:
            resp = self.search_news(q, max_results=min(max_results * 2, 20), days=search_days, topic="news")
            if not resp.success:
                errors.append(resp.error_message or "无结果")
            if resp.success:
                for r in resp.results:
                    if r.url not in seen_urls:
                        seen_urls.add(r.url)
                        all_results.append(r)

        fallback_used = False
        if not all_results and re.fullmatch(r"\d{6}", stock_code):
            try:
                from scripts.data_sources import isolated_call
                frame = isolated_call("akshare", "stock_news_em", {"symbol": stock_code}, 20)
                required = {"新闻标题", "新闻内容", "新闻链接", "发布时间"}
                if not required.issubset(frame.columns):
                    raise ValueError("东方财富新闻字段不完整")
                for item in frame.to_dict("records"):
                    all_results.append(SearchResult(
                        title=str(item["新闻标题"]), snippet=str(item["新闻内容"]),
                        url=str(item["新闻链接"]), source=str(item.get("新闻来源", "东方财富")),
                        # Provider timestamps are China local time, not UTC.
                        published_date=str(item["发布时间"]) + "+08:00",
                    ))
                fallback_used = bool(all_results)
            except Exception as exc:
                errors.append(f"东方财富新闻降级失败：{type(exc).__name__}")

        # 中文优先排序
        if prefer_chinese and all_results:
            chinese = [
                r
                for r in all_results
                if self._has_chinese(f"{r.title} {r.snippet}")
            ]
            if len(chinese) >= 2:
                all_results = chinese

        # 标题去重
        seen_titles: set = set()
        deduped = []
        for r in all_results:
            t = r.title.strip().rstrip("._ ")
            if t and t not in seen_titles:
                seen_titles.add(t)
                deduped.append(r)
        all_results = deduped

        # ── 日期过滤：剔除 Tavily 未按 days 参数正确过滤的过时结果 ──
        filtered = []
        for r in all_results:
            if r.published_date:
                pub = _parse_published_date(r.published_date)
                if pub is None:
                    r.published_date = None  # Explicitly shown as unverified in to_text.
                else:
                    age = datetime.now(timezone.utc) - pub
                    if age < timedelta(0) or age > timedelta(days=search_days):
                        continue
            # 剔除明显历史数据（标题含"招股说明书"、"首次公开发行"等）
            if re.search(
                r"招股说明书|首次公开发行|上市公告书|IPO",
                r.title,
            ):
                logger.debug(f"剔除历史文档: {r.title[:40]}")
                continue
            filtered.append(r)
        all_results = filtered

        # ── 对低质量摘要：网页正文提取 ──
        enhanced = []
        for r in all_results:
            needs_fetch = _is_noisy_snippet(r.snippet) or (
                not r.raw_content and len(r.snippet) < 60
            )
            if needs_fetch:
                logger.info(
                    f"[降噪] 摘要质量差，尝试获取正文: {r.url[:60]}..."
                )
                fetched = _fetch_content(r.url)
                if fetched and len(fetched) > 80:
                    r.snippet = fetched[:500]
                    r.raw_content = fetched[:500]
                    logger.info(
                        f"[降噪] 正文提取成功，{len(fetched)} 字符"
                    )
                else:
                    # 尽力从噪声文本中提取有用信息
                    cleaned = _extract_meaningful_text(r.snippet) if r.snippet else ""
                    if not cleaned or _is_noisy_snippet(cleaned):
                        r.snippet = "[链接] 点击查看原文"
                        r.raw_content = ""
                    else:
                        r.snippet = cleaned[:300]
                        r.raw_content = cleaned[:300]
                    logger.info(
                        f"[降噪] 无法获取正文，已清理摘要"
                    )
            enhanced.append(r)
            if len(enhanced) >= max_results:
                break
        all_results = enhanced

        response = SearchResponse(
            query=stock_name,
            results=all_results[:max_results],
            provider="东方财富(stock_news_em)" if fallback_used else "Tavily",
            success=bool(all_results),
            error_message="; ".join(dict.fromkeys(errors)) if not all_results else None,
            search_time=time.monotonic() - started,
        )
        return response

    @staticmethod
    def _is_foreign_stock(code: str) -> bool:
        c = code.strip().upper()
        if SearchService._US_STOCK_RE.match(c):
            return True
        if c.lower().startswith("hk") or (c.isdigit() and len(c) == 5):
            return True
        return False

    @staticmethod
    def _has_chinese(text: Optional[str]) -> bool:
        return bool(text and SearchService._CHINESE_RE.search(text))


def _extract_meaningful_text(text: str) -> str:
    """从噪声文本中提取有意义的行（去导航、去装饰）"""
    if not text:
        return ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    useful = []
    for l in lines:
        # 去掉纯导航/模板行
        if re.match(
            r"^(#+\s*(热点|工具|行情|资料|股东|财务|事项|栏目|实时)|"
            r"首页|财经|股票|基金|股吧|博客|登录|注册|"
            r"设为首页|加入收藏|导航|返回|管理自选)",
            l,
        ):
            continue
        if re.match(r"^[|/\-—\s]{3,}$", l):
            continue
        if re.match(r"^新浪|搜狐|东方财富|网易|中财网$", l):
            continue
        if len(l) < 6:
            continue
        # 保留含中文、日期、金额的行
        chinese = len(re.findall(r"[一-鿿]", l))
        has_date = bool(re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", l))
        has_money = bool(
            re.search(r"[万亿千]\b|\.\d{2}%|\d+\.?\d*亿元|\d+\.?\d*万元", l)
        )
        if chinese > 3 or has_date or has_money:
            useful.append(l)
    return "\n".join(useful).strip()[:500]


def _parse_published_date(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# === 便捷函数 ===
_service: Optional[SearchService] = None
_service_lock = threading.Lock()


def get_search_service(api_key: Optional[str] = None) -> SearchService:
    global _service
    if api_key is not None:
        return SearchService(tavily_keys=[api_key], news_max_age_days=7)
    if _service is None:
        with _service_lock:
            if _service is None:
                configured = api_key or os.environ.get("TAVILY_API_KEY", "")
                keys = [configured] if configured else []
                _service = SearchService(tavily_keys=keys, news_max_age_days=7)
    return _service


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )

    import sys

    service = get_search_service()
    code = sys.argv[1] if len(sys.argv) > 1 else "002011"
    name = sys.argv[2] if len(sys.argv) > 2 else "盾安环境"
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    resp = service.search_stock_news(code, name, max_results=5, days=days)
    print(resp.to_context())
