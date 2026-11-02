#!/usr/bin/env python3
"""Yahoo Finance 行情客户端。

不用 yfinance（版本频繁破坏、要拖 pandas），但保留了它最关键的一招：
用 curl_cffi 做浏览器 TLS 指纹伪装。Yahoo 会对裸 requests 的云端 IP 直接返回
HTTP 429，伪装后基本不再触发；装不上 curl_cffi 时自动退回 requests。

特性：
  * 浏览器指纹（可选）+ 多主机 + 尊重 Retry-After 的抖动退避
  * 429 冷却：一旦被限流，后续请求先等冷却时间，避免连环被拒
  * 统一的响应解析与错误类型，同时支持"最新价"与"日线序列"
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

import requests

from scripts.config import USER_AGENT, YAHOO_HOSTS

try:  # 浏览器指纹伪装，显著降低被 429 的概率
    from curl_cffi import requests as _browser_requests

    HAS_BROWSER_TLS = True
except Exception:  # noqa: BLE001 - 缺依赖时优雅降级
    _browser_requests = None
    HAS_BROWSER_TLS = False

TIMEOUT = 20
RETRIES = 3
BACKOFF_BASE = 4.0
BACKOFF_MAX = 25.0
COOLDOWN_AFTER_429 = 6.0


class YahooError(RuntimeError):
    """行情接口返回了无法使用的结果。"""


@dataclass(frozen=True)
class Quote:
    """一次成功的即时报价。"""

    price: float
    symbol: str
    market_time: datetime | None = None
    market_date: date | None = None
    currency: str = "USD"


_SESSION: Any = None
_COOLDOWN_UNTIL = 0.0


def transport_name() -> str:
    return "curl_cffi(chrome)" if HAS_BROWSER_TLS else "requests"


def session() -> Any:
    """优先用 curl_cffi 伪装 Chrome，回落普通 requests。"""
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if HAS_BROWSER_TLS:
        _SESSION = _browser_requests.Session(impersonate="chrome")
        _SESSION.headers.update(headers)
    else:
        _SESSION = requests.Session()
        _SESSION.headers.update(headers)
    return _SESSION


def _sleep_before_request() -> None:
    """429 冷却期内先等一会儿，避免连续撞墙。"""
    wait = _COOLDOWN_UNTIL - time.monotonic()
    if wait > 0:
        time.sleep(min(wait, 15))


def _note_rate_limited() -> None:
    global _COOLDOWN_UNTIL
    _COOLDOWN_UNTIL = time.monotonic() + COOLDOWN_AFTER_429


def _retry_after(resp: Any) -> float | None:
    raw = (resp.headers or {}).get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """带退避地请求 chart 接口，返回解析后的 JSON。"""
    last_error: Exception | None = None

    for attempt in range(RETRIES):
        _sleep_before_request()
        for host in YAHOO_HOSTS:
            url = f"{host}{path}"
            try:
                resp = session().get(url, params=params, timeout=TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - 传输层错误（两种会话都可能）
                last_error = exc
                continue

            status = resp.status_code
            if status == 200:
                try:
                    payload = resp.json()
                except ValueError as exc:
                    last_error = exc
                    continue
                if isinstance(payload, dict) and "chart" in payload:
                    return payload
                last_error = YahooError("响应缺少 chart 字段")
                continue

            if status == 429:
                last_error = YahooError("HTTP 429 限流")
                _note_rate_limited()
                wait = _retry_after(resp)
                if wait:
                    time.sleep(min(wait, 15))
                break  # 换主机没意义，直接进入下一轮退避

            last_error = YahooError(f"HTTP {status}")

        if attempt < RETRIES - 1:
            backoff = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
            time.sleep(backoff + random.uniform(0, 1.5))

    raise YahooError(f"请求失败: {last_error}")


def _chart_result(payload: dict[str, Any]) -> dict[str, Any]:
    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        raise YahooError(f"接口错误: {error}")
    results = chart.get("result") or []
    if not results:
        raise YahooError("接口返回空结果")
    result = results[0]
    if not isinstance(result, dict):
        raise YahooError("结果格式异常")
    return result


def _exchange_tz(meta: dict[str, Any]) -> timezone:
    offset = meta.get("gmtoffset")
    if isinstance(offset, (int, float)):
        return timezone(timedelta(seconds=int(offset)))
    return timezone.utc


def _meta_date(meta: dict[str, Any], ts: int) -> date:
    """行情时间戳 -> 交易所所在时区的自然日（用于即时报价）。"""
    return datetime.fromtimestamp(int(ts), tz=_exchange_tz(meta)).date()


# 期货日线的 timestamp 是"交易日开盘"时刻（美东 18:00 前一自然日），
# 直接取日期会把每个交易日标成前一天，周一还会标成周日。
SESSION_START_HOUR = 16


def _trading_date(meta: dict[str, Any], ts: int) -> date:
    """日线时间戳 -> 它真正所属的交易日。"""
    local = datetime.fromtimestamp(int(ts), tz=_exchange_tz(meta))
    day = local.date()
    if local.hour >= SESSION_START_HOUR:
        day += timedelta(days=1)
    while day.weekday() >= 5:  # 周六 / 周日 -> 顺延到周一
        day += timedelta(days=1)
    return day


def parse_quote(payload: dict[str, Any], symbol: str) -> Quote:
    """从 chart 响应里取最新价。"""
    result = _chart_result(payload)
    meta = result.get("meta") or {}

    price = meta.get("regularMarketPrice")
    if not isinstance(price, (int, float)) or price <= 0:
        # 退回到日线最后一个非空收盘
        closes = _closes(result)
        if not closes:
            raise YahooError("没有可用价格")
        price = closes[-1]

    market_time = None
    market_date = None
    ts = meta.get("regularMarketTime")
    if isinstance(ts, (int, float)):
        market_time = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        market_date = _meta_date(meta, int(ts))

    return Quote(
        price=float(price),
        symbol=symbol,
        market_time=market_time,
        market_date=market_date,
        currency=str(meta.get("currency") or "USD"),
    )


def _closes(result: dict[str, Any]) -> list[float]:
    """取出全部非空收盘价。"""
    indicators = result.get("indicators") or {}
    quote = indicators.get("quote") or []
    if not quote or not isinstance(quote[0], dict):
        return []
    raw = quote[0].get("close") or []
    return [float(v) for v in raw if isinstance(v, (int, float))]


def parse_daily(payload: dict[str, Any]) -> list[tuple[date, float]]:
    """从 chart 响应里取 (交易日, 收盘价) 序列，按日期升序。"""
    result = _chart_result(payload)
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []

    indicators = result.get("indicators") or {}
    quotes = indicators.get("quote") or []
    if not quotes or not isinstance(quotes[0], dict):
        raise YahooError("没有日线数据")
    closes = quotes[0].get("close") or []

    rows: dict[date, float] = {}
    for ts, close in zip(timestamps, closes):
        if not isinstance(ts, (int, float)):
            continue
        if not isinstance(close, (int, float)) or close <= 0:
            continue
        rows[_trading_date(meta, int(ts))] = float(close)

    return sorted(rows.items())


def fetch_quote(symbols: Sequence[str]) -> Quote | None:
    """依次尝试候选 symbol，返回第一个成功的报价。"""
    for symbol in symbols:
        try:
            payload = _get(
                f"/v8/finance/chart/{symbol}",
                {"range": "5d", "interval": "1d"},
            )
            return parse_quote(payload, symbol)
        except (YahooError, ValueError) as exc:
            print(f"    {symbol}: {exc}")
            continue
    return None


def fetch_daily(symbol: str, years: int = 2) -> list[tuple[date, float]]:
    """取最近 N 年的日线收盘。"""
    payload = _get(
        f"/v8/finance/chart/{symbol}",
        {"range": f"{years}y", "interval": "1d", "events": "div,split"},
    )
    return parse_daily(payload)


def fetch_many_daily(
    symbols: Iterable[str], years: int = 2
) -> dict[str, list[tuple[date, float]]]:
    """批量取日线，单个失败不影响其它。"""
    out: dict[str, list[tuple[date, float]]] = {}
    for symbol in symbols:
        try:
            out[symbol] = fetch_daily(symbol, years=years)
            print(f"    {symbol}: {len(out[symbol])} 个交易日")
        except (YahooError, ValueError) as exc:
            print(f"    {symbol}: 失败 ({exc})")
        time.sleep(0.6)  # 温和限速
    return out
