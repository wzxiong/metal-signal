#!/usr/bin/env python3
"""Yahoo Finance 行情客户端。

只依赖 requests，去掉了 yfinance（版本频繁破坏、要装 pandas）。
特性：
  * 多主机 + 指数退避重试
  * 统一的响应解析与错误类型
  * 同时支持"取最新价"与"取日线序列"
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

import requests

from scripts.config import USER_AGENT, YAHOO_HOSTS

TIMEOUT = 15
RETRIES = 3


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


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return s


_SESSION: requests.Session | None = None


def session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = _session()
    return _SESSION


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """带重试地请求 chart 接口，返回解析后的 JSON。"""
    last_error: Exception | None = None

    for attempt in range(RETRIES):
        for host in YAHOO_HOSTS:
            url = f"{host}{path}"
            try:
                resp = session().get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as exc:  # 网络层错误
                last_error = exc
                continue

            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except ValueError as exc:
                    last_error = exc
                    continue
                if isinstance(payload, dict) and "chart" in payload:
                    return payload
                last_error = YahooError("响应缺少 chart 字段")
                continue

            if resp.status_code == 429:
                # 触发限流，退避后重试
                last_error = YahooError("HTTP 429 限流")
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 2.0
                except ValueError:
                    wait = 2.0
                time.sleep(min(wait, 10))
                continue

            last_error = YahooError(f"HTTP {resp.status_code}")

        # 一轮所有主机都失败，退避
        if attempt < RETRIES - 1:
            time.sleep(1.5 * (attempt + 1))

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


def _meta_date(meta: dict[str, Any], ts: int) -> date:
    """把交易所时间戳换成交易所所在时区的自然日。"""
    offset = meta.get("gmtoffset")
    if isinstance(offset, (int, float)):
        tz = timezone(timedelta(seconds=int(offset)))
    else:
        tz = timezone.utc
    return datetime.fromtimestamp(int(ts), tz=tz).date()


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
        rows[_meta_date(meta, int(ts))] = float(close)

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
