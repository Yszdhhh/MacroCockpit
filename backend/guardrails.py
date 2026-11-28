"""Canonical Level-0 guardrails: forbidden jargon scrubbing, privacy/secret detection, safe regex, and SSRF protection."""
from __future__ import annotations

import html
import inspect
import ipaddress
import re
import socket
import urllib.parse
from pathlib import Path
from typing import Any, Callable

# Canonical forbidden jargon terms that must never leak into public UI, articles, or digests.
FORBIDDEN_TERMS: tuple[str, ...] = (
    "Sleeve",
    "E21",
    "E32",
    "E33",
    "Tier-1",
    "Regime",
    "arbitration",
    "测算口径",
    "公式解释",
    "公式说明",
    "逻辑解释",
    "联动说明",
    "方法论说明",
    "待补",
    "待刷新",
    "N/A",
)

_FORBIDDEN_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![A-Za-z0-9])E(?:21|32|33)(?![A-Za-z0-9])", re.IGNORECASE), "波动结构"),
    (re.compile(r"(?<![A-Za-z0-9])Tier-?1(?![A-Za-z0-9])", re.IGNORECASE), "核心"),
    (re.compile(r"(?<![A-Za-z0-9])Tier-?S(?![A-Za-z0-9])", re.IGNORECASE), "高优"),
    (re.compile(r"(?<![A-Za-z0-9])Tier-?A(?![A-Za-z0-9])", re.IGNORECASE), "重点"),
    (re.compile(r"(?<![A-Za-z0-9])Sleeves?(?![A-Za-z0-9])", re.IGNORECASE), "仓位"),
    (re.compile(r"(?<![A-Za-z0-9])Regimes?(?![A-Za-z0-9])", re.IGNORECASE), "市场周期"),
    (re.compile(r"(?<![A-Za-z0-9])arbitration(?![A-Za-z0-9])", re.IGNORECASE), "风险评估"),
    (re.compile(r"测算口径|公式解释|公式说明|逻辑解释|联动说明|方法论说明"), "参考摘要"),
    (re.compile(r"待刷新|待补充|待补"), "—"),
    (re.compile(r"(?<![A-Za-z0-9])N/A(?![A-Za-z0-9])", re.IGNORECASE), "—"),
)

_QUANT_THESIS_EXTRA_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"E33\+E32\s*液态孤立慢洗", re.IGNORECASE), "高流动性超跌结构"),
    (re.compile(r"(?<![A-Za-z0-9])(?:E33|E32)(?![A-Za-z0-9])", re.IGNORECASE), "超跌结构"),
    (re.compile(r"(?<![A-Za-z0-9])E21(?![A-Za-z0-9])", re.IGNORECASE), "清算风暴"),
    (re.compile(r"液态山寨"), "高流动性山寨"),
    (re.compile(r"MA50/MA120", re.IGNORECASE), "50/120日均线"),
    (re.compile(r"MA100", re.IGNORECASE), "100日均线"),
    (re.compile(r"（量化未确认，禁止提前抄底）"), "（趋势未确认，严禁左侧提前抄底）"),
    (re.compile(r"量化禁止左侧趋势做多"), "趋势未转强，严禁左侧提前做多"),
)

_EMOJI_RE = re.compile(
    r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u26FF\u2702-\u2712\u2716-\u27BF"
    r"\u231A-\u231B\u23E9-\u23FA\u25FD-\u25FE\u2B50\u2B55\uFE0E\uFE0F\u200D\u20E3]+"
)

_DEFAULT_SKIP_SANITIZE_KEYS: frozenset[str] = frozenset(
    {"url", "html_url", "filename", "id", "receipt_id", "sha256"}
)


def strip_emojis(text: Any) -> str:
    """Remove pictographic/color/flag/dingbat/keycap emojis while preserving checkmarks, arrows, and CJK punctuation."""
    s = _EMOJI_RE.sub("", str(text or ""))
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def scrub_forbidden(
    text: Any,
    *,
    preserve_newlines: bool = False,
    strip_emoji: bool = False,
    collapse_cjk_spaces: bool = False,
) -> str:
    """Replace internal quant jargon and forbidden placeholders with plain Chinese business wording."""
    raw = str(text or "")
    if strip_emoji:
        raw = strip_emojis(raw)
    if preserve_newlines:
        raw = raw.replace("\r\n", "\n")
    s = raw
    for rx, rep in _FORBIDDEN_REPLACEMENTS:
        s = rx.sub(rep, s)
    if collapse_cjk_spaces:
        s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)
    if preserve_newlines:
        lines = [re.sub(r"[ \t]{2,}", " ", ln).rstrip() for ln in s.splitlines()]
        return "\n".join(lines).strip()
    return re.sub(r"\s{2,}", " ", s).strip()


def scrub_quant_thesis(text: Any, *, preserve_newlines: bool = True) -> str:
    """Scrub quant-strategy thesis wording (signal_aggregator) before delegating to canonical scrub_forbidden."""
    if not text:
        return ""
    s = strip_emojis(html.unescape(str(text)))
    for rx, rep in _QUANT_THESIS_EXTRA_REPLACEMENTS:
        s = rx.sub(rep, s)
    return scrub_forbidden(s, preserve_newlines=preserve_newlines)


def sanitize_http_url_syntax(url: str) -> str:
    """Validate basic HTTP(S) URL syntax without performing DNS resolution."""
    s = str(url or "").strip()
    if not s.startswith(("https://", "http://")):
        return ""
    try:
        parsed = urllib.parse.urlsplit(s)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    return s


def sanitize_public_value(
    val: Any,
    *,
    key_name: str = "",
    skip_keys: frozenset[str] = _DEFAULT_SKIP_SANITIZE_KEYS,
    scrub_keys: bool = False,
    strip_emoji: bool = False,
    validate_url_key: bool = False,
    scrub_text: Callable[..., str] = scrub_forbidden,
) -> Any:
    """Recursively scrub forbidden terms across nested structures while preserving URLs and identifiers."""
    if isinstance(val, str):
        if key_name in skip_keys:
            if validate_url_key and key_name == "url":
                return sanitize_http_url_syntax(val)
            return val
        text = strip_emojis(val) if strip_emoji else val
        return scrub_text(text, preserve_newlines=(key_name == "body_md" or "\n" in val))
    if isinstance(val, list):
        return [
            sanitize_public_value(
                item,
                key_name=key_name,
                skip_keys=skip_keys,
                scrub_keys=scrub_keys,
                strip_emoji=strip_emoji,
                validate_url_key=validate_url_key,
                scrub_text=scrub_text,
            )
            for item in val
        ]
    if isinstance(val, tuple):
        return [
            sanitize_public_value(
                item,
                key_name=key_name,
                skip_keys=skip_keys,
                scrub_keys=scrub_keys,
                strip_emoji=strip_emoji,
                validate_url_key=validate_url_key,
                scrub_text=scrub_text,
            )
            for item in val
        ]
    if isinstance(val, dict):
        out: dict[str, Any] = {}
        for k, v in val.items():
            k_str = scrub_forbidden(str(k), strip_emoji=strip_emoji) if scrub_keys else str(k)
            out[k_str] = sanitize_public_value(
                v,
                key_name=k_str,
                skip_keys=skip_keys,
                scrub_keys=scrub_keys,
                strip_emoji=strip_emoji,
                validate_url_key=validate_url_key,
                scrub_text=scrub_text,
            )
        return out
    return val


# --- Privacy & Secrets Redline ---

PRIVATE_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"journal", "portfolio", "preferences", "workspace", "credentials"}
)

FORBIDDEN_AUDIT_KEYS: frozenset[str] = frozenset(
    {
        "token",
        "access_token",
        "read_token",
        "rw_token",
        "sync_token",
        "secret",
        "password",
        "authorization",
        "cookie",
        "amount",
        "qty",
        "price",
        "entry_price",
        "mark_price",
        "margin",
        "margin_usd",
        "margin_amount",
        "upl",
        "equity",
        "net_equity",
        "notional",
        "debt",
        "confirmed_debt_usd",
        "raw_text",
        "body",
        "text",
        "csv",
    }
)

_PRIVATE_ARTICLE_TEXT = re.compile(
    r"占\s*(?:总\s*)?净值|净值\s*占比|portfolio_exposure"
    r"|(?:现货|合约|实际|私人)持仓明细"
    r"|(?:当前|目前|个人|账户|实盘)\s*(?:持仓|仓位|权益|现金)\s*[：:=]"
    r"|(?:我的|本人|个人|实际|我在[\u4e00-\u9fffA-Za-z0-9]{0,16}的)\s*(?:持仓|仓位|余额|净值|盈亏|成本|本金)"
    r"|(?:我|本人|个人|咱们)\s*(?:目前|当前|现在|手里|手头|账户里?)?\s*(?:持有|买了|买入|建仓|加仓|重仓|满仓|做多|做空|套牢)\s*[^\n。；;]{0,28}?(?:\d[\d,.]*\s*(?:万|千|亿)?\s*(?:美元|美金|人民币|港元|港币|元|股|手|张|枚|份|USDT|USD|CNY|RMB|HKD)|股票|仓位|多单|空单)"
    r"|\bI\s+(?:currently\s+)?(?:hold|own|bought|shorted)\s+\$?\d[\d,.]*\b"
    r"|(?:账户\s*(?:持仓|仓位|余额|净值|盈亏|成本|本金)|持仓金额|持仓市值|仓位金额|账户金额|保证金金额)\s*(?:为|是|有|约|达|[:：=])?\s*\d"
    r"|(?:手头现金|可用现金|现金余额|保证金余额)\s*[：:=]?\s*[+-]?\s*(?:HK\$|US\$|CN¥|RMB|HKD|USD|CNY|USDT|USDC|[$¥￥€£])?\s*[+-]?\s*\d"
    r"|\b(?:my\s+(?:actual\s+)?(?:portfolio|holdings?|positions?|trades?|account(?:\s+balance)?)|account\s+balance\s*(?:is|:|=)?\s*[+-]?\s*\$?\d[\d,.]*)\b"
    r"|\b(?:current|personal|private|actual|live|account)\s+(?:portfolio|holdings?|positions?)\s*[:=]"
    r"|\b(?:cash\s+on\s+hand|available\s+cash|cash\s+balance|margin\s+balance|net\s+liquidation|account\s+equity)(?:\s+is|\s*[:=])?\s*[+-]?\s*(?:HK\$|US\$|CN¥|RMB|HKD|USD|CNY|USDT|USDC|[$¥￥€£])?\s*[+-]?\s*\d"
    r"|\b[A-Z]{2,10}\s+\d[\d,.]*\s+(?:shares?|contracts?|lots?)\b"
    r"|\b(?:bought|sold|holding)\s+\d[\d,.]*\s+(?:shares?|contracts?|lots?|btc|eth|sol|bnb|paxg)\b[^.\n]{0,40}\b(?:at|@)\s*\$?\d"
    r"|\b(?:unrealized\s+p(?:&|n)l|liquidation\s+price)\s*(?:is|:|=)?\s*[+-]?\$?\d"
    r"|(?<![A-Z0-9_-])(?:sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{16,}|Bearer\s+|gh[pousr]_[A-Z0-9]{10,}|xox[baprs]-[A-Z0-9-]{10,}|AKIA[0-9A-Z]{16})"
    r"|(?<![A-Z0-9])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|(?<![A-Z0-9])(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|secret)\s*[\x22\x27]?\s*[:=]",
    re.IGNORECASE,
)

_PRIVATE_DEBATE_TEXT = re.compile(
    r"[$＄¥￥€£]|\b(?:token|secret|api[_ -]?key|authorization)\b"
    r"|(?:USD|CNY|RMB)\s*[-+]?\d|\d\s*(?:美元|人民币|元(?!素)|USD|CNY|RMB|USDT)"
    r"|净值\s*[-+]?\d+(?:\.\d+)?\s*[%％]"
    r"|(?:总净值|账户净值|持仓金额|持仓数量|合约名义|现货多头|合约空头|实际持仓)",
    re.IGNORECASE,
)

_PRIVATE_TRIGGER_NEUTRALIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"占\s*(?:总\s*)?净值|净值\s*占比|portfolio_exposure|(?:现货|合约|实际|私人)持仓明细", re.IGNORECASE), "市场参考"),
    (re.compile(r"(?<![A-Z0-9_-])(?:sk-[A-Za-z0-9_-]*|Bearer\s+[A-Za-z0-9._-]+|gh[pousr]_[A-Za-z0-9]+)", re.IGNORECASE), "[凭证脱敏]"),
    (re.compile(r"(?<![A-Z0-9])(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|secret)\s*([\"\']?)\s*[:=]", re.IGNORECASE), r"\1\2（字段）"),
)


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def has_private_article_text(text: str) -> bool:
    return bool(_PRIVATE_ARTICLE_TEXT.search(text or ""))


def has_private_debate_text(text: str) -> bool:
    return has_private_article_text(text) or bool(_PRIVATE_DEBATE_TEXT.search(text or ""))


def has_private_article_content(article: Any) -> bool:
    if not isinstance(article, dict):
        return False
    filename = article.get("filename")
    is_debate = isinstance(filename, str) and filename.lower().startswith("ta_")
    check = has_private_debate_text if is_debate else has_private_article_text
    return any(check(text) for text in _iter_strings(article))


def neutralize_private_triggers(text: str) -> str:
    """Scrub forbidden jargon and neutralize accidental private-regex triggers in public third-party articles."""
    s = scrub_forbidden(text, preserve_newlines=True)
    for rx, rep in _PRIVATE_TRIGGER_NEUTRALIZERS:
        s = rx.sub(rep, s)
    return s


def safe_article_filename(value: Any) -> str | None:
    """Validate strict basename-only markdown article filename."""
    if not isinstance(value, str):
        return None
    if (
        not value
        or len(value) > 180
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or ".." in value
        or Path(value).name != value
        or not value.lower().endswith(".md")
        or "异常告警" in value
    ):
        return None
    return value


def private_article_filenames(articles_dir: Path | list[dict[str, Any]]) -> set[str]:
    """Identify private article filenames from a directory or a list of article records."""
    if isinstance(articles_dir, list):
        return {
            name for article in articles_dir
            if has_private_article_content(article) and (name := safe_article_filename(article.get("filename")))
        }
    private: set[str] = set()
    if not articles_dir.is_dir():
        return private
    for path in articles_dir.glob("*.md"):
        name = safe_article_filename(path.name)
        if not name:
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            private.add(name)
            continue
        check = has_private_debate_text if name.lower().startswith("ta_") else has_private_article_text
        if check(body):
            private.add(name)
    return private


# --- Bounded Safe Regex Validator (ReDoS Protection) ---

MAX_REGEX_LEN = 80
_UNSAFE_REGEX_TOKENS: tuple[str, ...] = (
    "(?=",
    "(?!",
    "(?<=",
    "(?<!",
    "(?>",
    "(?(",
    "(?P=",
    "*+",
    "++",
    "?+",
)


def check_safe_regex(pattern: str) -> re.Pattern[str]:
    """Validate bounded safe regex subset rejecting lookarounds, backreferences, and nested quantifiers."""
    if not pattern or len(pattern) > MAX_REGEX_LEN:
        raise ValueError(f"正则表达式长度须在 1~{MAX_REGEX_LEN} 字符之间")
    for tok in _UNSAFE_REGEX_TOKENS:
        if tok in pattern:
            raise ValueError(f"正则表达式包含不允许的高风险语法: {tok}")
    if re.search(r"\\[1-9]", pattern):
        raise ValueError("正则表达式不支持反向引用")

    stack: list[bool] = [False]
    alt_stack: list[bool] = [False]
    branch_repeats = [0]
    max_repeats = [0]
    i = 0
    n = len(pattern)
    prev_was_quantifier = False
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            prev_was_quantifier = False
            i += 2
            continue
        if ch == "[":
            prev_was_quantifier = False
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n:
                if pattern[j] == "\\":
                    j += 2
                    continue
                if pattern[j] == "]":
                    break
                j += 1
            if j >= n:
                raise ValueError("正则表达式字符集未闭合")
            i = j + 1
            continue
        if ch == "(":
            stack.append(False)
            alt_stack.append(False)
            branch_repeats.append(0)
            max_repeats.append(0)
            prev_was_quantifier = False
            if i + 2 < n and pattern[i + 1 : i + 3] == "?:":
                i += 3
            elif pattern[i + 1 : i + 2] == "?":
                raise ValueError("正则表达式不支持此分组语法")
            else:
                i += 1
            continue
        if ch == "|":
            alt_stack[-1] = True
            max_repeats[-1] = max(max_repeats[-1], branch_repeats[-1])
            branch_repeats[-1] = 0
            prev_was_quantifier = False
            i += 1
            continue
        if ch == ")":
            if len(stack) <= 1:
                raise ValueError("正则表达式括号不匹配")
            group_had_quant = stack.pop()
            group_had_alt = alt_stack.pop()
            group_repeats = max(max_repeats.pop(), branch_repeats.pop())
            branch_repeats[-1] += group_repeats
            nxt = pattern[i + 1] if i + 1 < n else ""
            if group_had_alt and nxt in ("*", "+", "{"):
                raise ValueError("正则表达式禁止对交替分支组使用重复量词")
            if nxt in ("*", "+", "?", "{"):
                if group_had_quant:
                    raise ValueError("正则表达式禁止嵌套量词")
                stack[-1] = True
            elif group_had_quant:
                stack[-1] = True
            if group_had_alt:
                alt_stack[-1] = True
            prev_was_quantifier = False
            i += 1
            continue
        if ch in ("*", "+", "{"):
            if prev_was_quantifier:
                raise ValueError("正则表达式禁止连续重复量词")
            variable = True
            if ch == "{":
                close_idx = pattern.find("}", i + 1)
                if close_idx == -1:
                    raise ValueError("正则表达式大括号未闭合")
                inner_brace = pattern[i + 1 : close_idx]
                if not re.fullmatch(r"[0-9]+(?:,[0-9]+)?", inner_brace):
                    raise ValueError("正则表达式量词须指定有限上界")
                bounds = [int(value) for value in inner_brace.split(",")]
                if max(bounds) > 100:
                    raise ValueError("正则表达式量词上界不得超过 100")
                variable = bounds[0] != bounds[-1]
                i = close_idx
            branch_repeats[-1] += int(variable)
            stack[-1] = True
            prev_was_quantifier = True
            i += 1
            continue
        if ch == "?":
            if prev_was_quantifier:
                prev_was_quantifier = False
            else:
                branch_repeats[-1] += 1
                stack[-1] = True
                prev_was_quantifier = True
            i += 1
            continue

        prev_was_quantifier = False
        i += 1

    if len(stack) != 1:
        raise ValueError("正则表达式括号未闭合")
    if max(max_repeats[0], branch_repeats[0]) > 1:
        raise ValueError("同一正则分支最多允许一个可变重复量词")

    try:
        return re.compile(pattern, re.IGNORECASE)
    except (re.error, OverflowError) as exc:
        raise ValueError(f"正则表达式语法错误: {exc}") from exc


# --- SSRF Guard & Pinned No-Redirect Fetch ---

_CLASH_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")


def is_forbidden_ip(
    ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_clash_fake_ip: bool = False,
) -> bool:
    if allow_clash_fake_ip and ip_obj in _CLASH_FAKE_IP_NET:
        return False
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def resolve_safe_public_ips(url: str, *, socket_mod: Any = socket) -> tuple[bool, list[str]]:
    """Validate URL safety once via DNS and return (ok, pinned_public_ips)."""
    raw = str(url or "").strip()
    if not raw:
        return (False, [])
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError:
        return (False, [])
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return (False, [])
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return (False, [])
    if host in ("localhost", "ip6-localhost") or host.endswith((".local", ".internal", ".localhost")):
        return (False, [])
    host_unbracketed = host.strip("[]")
    try:
        ip = ipaddress.ip_address(host_unbracketed)
        if is_forbidden_ip(ip, allow_clash_fake_ip=False):
            return (False, [])
        return (True, [str(ip)])
    except ValueError:
        resolved: list[str] = []
        try:
            dns_port = port if port is not None else (443 if scheme == "https" else 80)
            addrs = socket_mod.getaddrinfo(host_unbracketed, dns_port, type=socket.SOCK_STREAM)
            for _fam, _type, _proto, _canon, sockaddr in addrs:
                if not sockaddr:
                    continue
                ip_str = str(sockaddr[0]).split("%", 1)[0]
                resolved_ip = ipaddress.ip_address(ip_str)
                if is_forbidden_ip(resolved_ip, allow_clash_fake_ip=True):
                    return (False, [])
                if ip_str not in resolved:
                    resolved.append(ip_str)
        except OSError:
            pass
        return (True, resolved)


def is_safe_public_url(url: str, *, socket_mod: Any = socket) -> bool:
    """Block non-HTTP(S), localhost, .local/.internal, and private/loopback/reserved IP literals."""
    ok, _ips = resolve_safe_public_ips(url, socket_mod=socket_mod)
    return ok


def _supports_allow_redirects(getter: Any) -> bool:
    try:
        target = getattr(getter, "side_effect", None)
        if callable(target):
            getter = target
        sig = inspect.signature(getter)
        return any(
            p.name == "allow_redirects" or p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
    except (TypeError, ValueError):
        return False


def make_pinned_adapter(expected_host: str, pinned_ip: str):
    """Build an HTTPAdapter whose underlying socket connects strictly to pinned_ip while preserving TLS SNI."""
    from requests.adapters import HTTPAdapter
    from urllib3 import PoolManager
    from urllib3.connection import HTTPConnection, HTTPSConnection
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
    from urllib3.util import connection as urllib3_util_conn

    target_host = expected_host.strip().lower().rstrip(".")

    class _PinnedHTTPConn(HTTPConnection):
        def _new_conn(self):
            cur_host = str(getattr(self, "host", "") or "").strip().lower().rstrip(".")
            if cur_host == target_host:
                if hasattr(self, "_dns_host"):
                    orig = self._dns_host
                    self._dns_host = pinned_ip
                    try:
                        return super()._new_conn()
                    finally:
                        self._dns_host = orig
                return urllib3_util_conn.create_connection(
                    (pinned_ip, self.port),
                    self.timeout,
                    source_address=self.source_address,
                    socket_options=self.socket_options,
                )
            return super()._new_conn()

    class _PinnedHTTPSConn(HTTPSConnection):
        def _new_conn(self):
            cur_host = str(getattr(self, "host", "") or "").strip().lower().rstrip(".")
            if cur_host == target_host:
                if hasattr(self, "_dns_host"):
                    orig = self._dns_host
                    self._dns_host = pinned_ip
                    try:
                        return super()._new_conn()
                    finally:
                        self._dns_host = orig
                return urllib3_util_conn.create_connection(
                    (pinned_ip, self.port),
                    self.timeout,
                    source_address=self.source_address,
                    socket_options=self.socket_options,
                )
            return super()._new_conn()

    class _PinnedHTTPPool(HTTPConnectionPool):
        ConnectionCls = _PinnedHTTPConn

    class _PinnedHTTPSPool(HTTPSConnectionPool):
        ConnectionCls = _PinnedHTTPSConn

    class _PinnedPoolManager(PoolManager):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.pool_classes_by_scheme = {"http": _PinnedHTTPPool, "https": _PinnedHTTPSPool}

    class _PinnedHTTPAdapter(HTTPAdapter):
        def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
            self._pool_connections = connections
            self._pool_maxsize = maxsize
            self._pool_block = block
            self.poolmanager = _PinnedPoolManager(
                num_pools=connections,
                maxsize=maxsize,
                block=block,
                **pool_kwargs,
            )

    return _PinnedHTTPAdapter()


def http_get_no_redirect(
    url: str,
    *,
    headers: dict[str, str],
    timeout: int,
    pinned_ip: str | None = None,
    requests_mod: Any = None,
) -> Any:
    """Perform a redirect-disabled GET, optionally pinning DNS IP for real requests.Session."""
    req = requests_mod
    if req is None:
        import requests as req  # type: ignore[no-redef]
    getter = getattr(req, "get", None)
    is_real_requests = (
        callable(getter)
        and getattr(getter, "__module__", "").startswith("requests.")
        and type(getter).__name__ == "function"
    )
    if pinned_ip and is_real_requests and hasattr(req, "Session"):
        host = (urllib.parse.urlsplit(url).hostname or "").strip().lower().rstrip(".")
        with req.Session() as sess:
            adapter = make_pinned_adapter(host, pinned_ip)
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            return sess.get(url, headers=headers, timeout=timeout, allow_redirects=False)

    kw: dict[str, Any] = {"headers": headers, "timeout": timeout}
    if _supports_allow_redirects(getter):
        kw["allow_redirects"] = False
    try:
        return req.get(url, **kw)
    except TypeError:
        kw.pop("allow_redirects", None)
        return req.get(url, **kw)


def safe_fetch_public_url(
    url: str,
    *,
    headers: dict[str, str],
    timeout: int = 10,
    max_hops: int = 3,
    requests_mod: Any = None,
    socket_mod: Any = socket,
    is_allowed_url: Callable[[str], bool] | None = None,
) -> Any:
    """Validate every hop against SSRF (DNS + IP pinning + manual redirect validation) and return response or None."""
    cur_url = str(url or "").strip()
    for _hop in range(max_hops + 1):
        ok, ips = resolve_safe_public_ips(cur_url, socket_mod=socket_mod)
        if not ok:
            return None
        if is_allowed_url is not None and not is_allowed_url(cur_url):
            return None
        pinned = ips[0] if ips else None
        resp = http_get_no_redirect(
            cur_url,
            headers=headers,
            timeout=timeout,
            pinned_ip=pinned,
            requests_mod=requests_mod,
        )
        status = int(getattr(resp, "status_code", 0) or 0)
        if status in (301, 302, 303, 307, 308):
            response_headers = getattr(resp, "headers", None) or {}
            loc = str(response_headers.get("Location") or response_headers.get("location") or "").strip()
            if not loc:
                return resp
            cur_url = urllib.parse.urljoin(cur_url, loc)
            continue
        return resp
    return None
