import re
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import streamlit as st
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
import graphviz
import streamlit.components.v1 as components

# Optional dependency (better-looking interactive network graph)
try:
    from pyvis.network import Network
except Exception:
    Network = None

# ------------------------------------------------------------
# CONFIG & STYLING
# ------------------------------------------------------------
st.set_page_config(
    page_title="GEO Checker",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)


st.markdown(
    """
<style>
    /* Generel Baggrund */
    .stApp { background-color: #fff; }

    /* Card Styling */
    .css-card {
        background: linear-gradient(90deg, #2563eb 0%, #88b0f1 100%) !important;
        border-radius: 12px;
        padding: 1px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        border: 1px solid #e9ecef;
        margin-bottom: 20px;
        height: 100%;
    }

    /* Overskrifter */
    h1, h2, h3 { color: #1e293b; font-family: 'Helvetica Neue', sans-serif; }
    h4 {
        color: #475569;
        font-size: 14px;
        text-transform: uppercase;
        margin-bottom: 10px;
        letter-spacing: 0.5px;
    }

    /* Metrikker */
    div[data-testid="stMetricValue"] { font-size: 28px; color: #0f172a; font-weight: 700; }
    div[data-testid="stMetricLabel"] { color: #64748b; font-size: 14px; }

    /* Severity Badges */
    .badge {
        padding: 4px 10px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 11px;
        color: white;
        display: inline-block;
        margin-right: 8px;
    }
    .badge-Critical { background-color: #ef4444; }
    .badge-High { background-color: #f97316; }
    .badge-Medium { background-color: #eab308; color: #fff; }
    .badge-Low { background-color: #22c55e; }

    /* Status Lists (Found/Missing) */
    .status-item {
        padding: 4px 0;
        display: flex;
        align-items: center;
        font-size: 14px;
        color: #334155;
    }
    .status-icon {
        margin-right: 8px;
        font-weight: bold;
        font-size: 16px;
    }
    .found { color: #16a34a; }
    .missing { color: #dc2626; }
    .optional { color: #94a3b8; }

    /* Expander styling */
    .streamlit-expanderHeader {
        background-color: #ffffff;
        border-radius: 8px;
        border: 1px solid #f1f5f9;
    }
        /* ---------- Primary CTA button styling (blue, not red) ---------- */
    .stButton > button {
        background: linear-gradient(90deg, #2563eb 0%, #3b82f6 100%) !important;
        color: #ffffff !important;
        border: 0 !important;
        border-radius: 12px !important;
        padding: 12px 18px !important;
        font-weight: 700 !important;
        font-size: 14px !important;
        box-shadow: 0 10px 22px rgba(37, 99, 235, 0.25) !important;
        transition: transform 120ms ease, box-shadow 120ms ease, filter 120ms ease;
        width: 100% !important;
        max-width: 520px !important;
        margin: 10px auto !important;
        display: block !important;
    }
    .stButton > button:hover {
        filter: brightness(1.03);
        transform: translateY(-1px);
        box-shadow: 0 14px 28px rgba(37, 99, 235, 0.30) !important;
    }
    .stButton > button:active {
        transform: translateY(0px);
        box-shadow: 0 10px 22px rgba(37, 99, 235, 0.22) !important;
    }
    /* Tabs: make active accent blue (avoid red underline) */
    div[data-baseweb="tab"][aria-selected="true"] > div {
        color: #2563eb !important;
    }
    div[data-baseweb="tab-highlight"] {
        background-color: #2563eb !important;
    }
    .st-emotion-cache-xhkv9f {
        margin:auto;
    }

    /* ---------- Radio buttons: force blue accent instead of red ---------- */
    input[type="radio"] {
        accent-color: #2563eb !important; /* Tailwind blue-600 */
    }

    /* Streamlit-specific fallback (older WebKit) */
    div[role="radiogroup"] svg {
        color: #2563eb !important;
        fill: #2563eb !important;
    }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# --- SIKKERHEDSTJEK (START) ---
# ---------------------------------------------------------
# Vi tjekker om URL'en indeholder vores hemmelige nøgle
query_params = st.query_params  # Henter parametre fra URL'en

# Hvis nøglen mangler eller er forkert, stop appen
if query_params.get("access") != "GeneraxionKey":
    st.error("⛔ Adgang nægtet.")
    st.info("Denne app kan kun tilgås gennem Generaxions interne systemer.")
    st.stop() # Stopper koden her, så resten ikke vises
# ---------------------------------------------------------
# --- SIKKERHEDSTJEK (SLUT) ---
# ---------------------------------------------------------


# ------------------------------------------------------------
# Models
# ------------------------------------------------------------
@dataclass
class Finding:
    pillar: str  # Entity Authority | Content Credibility | Technical Signals
    severity: str  # Critical | High | Medium | Low
    title: str
    why: str
    how: str
    impact: int  # 1-5
    effort_minutes: int
    evidence: Optional[str] = None
    snippet: Optional[str] = None


# ------------------------------------------------------------
# Utilities & Helpers
# ------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def safe_json_loads(s: str) -> Optional[Any]:
    try:
        return json.loads(s)
    except Exception:
        return None

def norm_schema_type(t: str) -> str:
    if not t:
        return ""
    t = t.replace("http://schema.org/", "").replace("https://schema.org/", "")
    return t.strip()

def get_hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


# ------------------------------------------------------------
# Fetching
# ------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=60 * 30)
def fetch_url_cached(url: str) -> Tuple[str, str, int, Dict[str, str]]:
    return fetch_url_uncached(url)

def fetch_url_uncached(url: str) -> Tuple[str, str, int, Dict[str, str]]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "da,en-US;q=0.8,en;q=0.7",
    }
    try:
        r = requests.get(url, headers=headers, timeout=25, allow_redirects=True)
        if not r.encoding:
            r.encoding = r.apparent_encoding
        return r.url, r.text or "", r.status_code, dict(r.headers)
    except Exception as e:
        return url, "", 0, {"Error": str(e)}


def build_from_paste(pasted_content: str) -> Tuple[str, str, int, Dict[str, str]]:
    if "<" not in pasted_content and ">" not in pasted_content:
        html = (
            "<html><head><title></title></head>"
            "<body><main><p>"
            + BeautifulSoup(pasted_content, "html.parser").get_text(" ", strip=True)
            + "</p></main></body></html>"
        )
        return "(pasted)", html, 200, {}
    return "(pasted)", pasted_content, 200, {}


# ------------------------------------------------------------
# Parsing
# ------------------------------------------------------------
def extract_main_text_and_title(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(" ", strip=True)

    # Fjern støj (men lad footer/header blive i teksten via soup.get_text hvis main ikke findes)
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    main = soup.find("main") or soup.find("article")
    text = (main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True))
    text = re.sub(r"\s+", " ", text).strip()
    return text, title

def extract_headings(html: str) -> Dict[str, List[str]]:
    soup = BeautifulSoup(html, "html.parser")
    out: Dict[str, List[str]] = {"h1": [], "h2": [], "h3": []}
    for h in ["h1", "h2", "h3"]:
        out[h] = [x.get_text(" ", strip=True) for x in soup.find_all(h) if x.get_text(strip=True)]
    return out

def extract_links(html: str, base_url: str = "") -> Tuple[List[str], List[str]]:
    soup = BeautifulSoup(html, "html.parser")
    internal: List[str] = []
    external: List[str] = []

    base_host = get_hostname(base_url) if base_url else ""

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue

        if href.startswith(("http://", "https://")):
            h = get_hostname(href)
            if base_host and h and h == base_host:
                internal.append(href)
            else:
                external.append(href)
        else:
            internal.append(href)

    return list(dict.fromkeys(internal)), list(dict.fromkeys(external))

def extract_jsonld(html: str) -> List[Any]:
    soup = BeautifulSoup(html, "html.parser")
    out: List[Any] = []
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = s.string
        if not raw:
            continue
        clean_raw = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", raw.strip())
        data = safe_json_loads(clean_raw)
        if data is None:
            raw2 = re.sub(r",\s*([}\]])", r"\1", clean_raw)
            data = safe_json_loads(raw2)
        if data is not None:
            out.append(data)
    return out

def flatten_schema_types(jsonld: List[Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
    types: List[str] = []
    objs: List[Dict[str, Any]] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if "@type" in x:
                t = x.get("@type")
                if isinstance(t, str):
                    types.append(t)
                elif isinstance(t, list):
                    for tt in t:
                        if isinstance(tt, str):
                            types.append(tt)
            if "@context" in x and "@type" in x:
                objs.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    for n in jsonld:
        walk(n)

    norm = sorted({norm_schema_type(str(t)) for t in types if str(t).strip()})
    return norm, objs

def extract_meta(html: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    out: Dict[str, str] = {}

    def get_meta_name(name: str) -> str:
        tag = soup.find("meta", attrs={"name": name})
        return (tag.get("content") or "").strip() if tag else ""

    def get_meta_prop(prop: str) -> str:
        tag = soup.find("meta", attrs={"property": prop})
        return (tag.get("content") or "").strip() if tag else ""

    out["description"] = get_meta_name("description")
    out["robots"] = get_meta_name("robots")
    out["og:title"] = get_meta_prop("og:title")
    out["og:site_name"] = get_meta_prop("og:site_name")
    out["og:type"] = get_meta_prop("og:type")
    out["og:url"] = get_meta_prop("og:url")
    out["og:description"] = get_meta_prop("og:description")

    link = soup.find("link", rel=lambda x: x and "canonical" in x)
    out["canonical"] = (link.get("href") or "").strip() if link else ""

    return out

# --------------------- Indexability/robots helpers ---------------------
def parse_robots_directives(value: str) -> List[str]:
    if not value:
        return []
    # Split on commas/semicolons and normalize
    parts = re.split(r"[,;]", value)
    return sorted({p.strip().lower() for p in parts if p and p.strip()})

@st.cache_data(show_spinner=False, ttl=60 * 60)
def fetch_robots_txt(host_url: str) -> Tuple[int, str]:
    """Fetch robots.txt. Returns (status_code, text)."""
    try:
        base = host_url.rstrip("/")
        robots_url = f"{base}/robots.txt"
        r = requests.get(
            robots_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"},
            timeout=15,
            allow_redirects=True,
        )
        return r.status_code, r.text or ""
    except Exception:
        return 0, ""

def robots_txt_allows(url: str, robots_txt: str) -> Tuple[Optional[bool], Optional[str]]:
    """Very small heuristic parser for User-agent:* rules.

    Returns (allowed?, matched_rule). allowed? is:
      - True/False if we could evaluate a matching rule
      - None if robots.txt is empty/unparseable
    """
    if not robots_txt or not url:
        return None, None

    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    lines = []
    for raw in robots_txt.splitlines():
        ln = raw.split("#", 1)[0].strip()
        if ln:
            lines.append(ln)

    # Collect rules in UA groups; we only consider User-agent: *
    in_star_group = False
    disallows: List[str] = []
    allows: List[str] = []

    for ln in lines:
        low = ln.lower()
        if low.startswith("user-agent:"):
            ua = ln.split(":", 1)[1].strip()
            in_star_group = (ua == "*")
            continue
        if not in_star_group:
            continue
        if low.startswith("disallow:"):
            rule = ln.split(":", 1)[1].strip()
            disallows.append(rule)
        elif low.startswith("allow:"):
            rule = ln.split(":", 1)[1].strip()
            allows.append(rule)

    if not disallows and not allows:
        return None, None

    def matches(rule: str, p: str) -> bool:
        if rule is None:
            return False
        rule = rule.strip()
        # Empty Disallow means allow all
        if rule == "":
            return False
        return p.startswith(rule)

    # Longest-match wins (common robots heuristic)
    best_allow = ""
    for r in allows:
        if r and matches(r, path) and len(r) > len(best_allow):
            best_allow = r

    best_disallow = ""
    for r in disallows:
        if r and matches(r, path) and len(r) > len(best_disallow):
            best_disallow = r

    if best_allow and (len(best_allow) >= len(best_disallow)):
        return True, f"Allow: {best_allow}"
    if best_disallow:
        return False, f"Disallow: {best_disallow}"

    return True, None

def compute_indexability(final_url: str, status: int, meta: Dict[str, str], headers: Dict[str, str]) -> Dict[str, Any]:
    """Compute basic indexability signals (heuristic)."""
    meta_robots_raw = (meta.get("robots") or "").strip()

    # Header keys can vary in casing
    x_robots_raw = ""
    for k, v in (headers or {}).items():
        if k.lower() == "x-robots-tag":
            x_robots_raw = (v or "").strip()
            break

    meta_dirs = parse_robots_directives(meta_robots_raw)
    x_dirs = parse_robots_directives(x_robots_raw)

    meta_noindex = "noindex" in meta_dirs
    header_noindex = "noindex" in x_dirs

    # robots.txt check (only when we have a real URL)
    robots_status, robots_txt = (0, "")
    robots_allows, robots_rule = (None, None)
    if final_url and final_url.startswith(("http://", "https://")):
        base = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}"
        robots_status, robots_txt = fetch_robots_txt(base)
        robots_allows, robots_rule = robots_txt_allows(final_url, robots_txt)

    # Decide
    blocked_reasons: List[str] = []
    if status and status >= 400:
        blocked_reasons.append(f"HTTP {status}")
    if meta_noindex:
        blocked_reasons.append("meta robots=noindex")
    if header_noindex:
        blocked_reasons.append("X-Robots-Tag=noindex")
    if robots_allows is False:
        blocked_reasons.append("robots.txt disallow")

    # High-level label
    if meta_noindex or header_noindex:
        label = "Noindex"
    elif status and status >= 400:
        label = "Not reachable"
    elif robots_allows is False:
        label = "Blocked by robots.txt"
    elif status in (200, 201, 202) and not blocked_reasons:
        label = "Indexable"
    else:
        label = "Uncertain"

    return {
        "label": label,
        "blocked": bool(blocked_reasons),
        "blocked_reasons": blocked_reasons,
        "status": status,
        "meta_robots": meta_robots_raw,
        "x_robots_tag": x_robots_raw,
        "robots_txt_status": robots_status,
        "robots_txt_allows": robots_allows,
        "robots_txt_rule": robots_rule,
    }

def find_nap_signals(html: str) -> Dict[str, Optional[str]]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    # CVR (robust DK formats)
    cvr = None
    # Accept: "CVR 12345678", "CVR: 12345678", "CVR nr 12 34 56 78", "CVR-nr. 12345678", etc.
    m = re.search(
        r"\bCVR\s*(?:[-\s]*nr\.?\s*)?[:.]?\s*(\d(?:\s*\d){7})\b",
        text,
        re.I,
    )
    if m:
        cvr = re.sub(r"\s+", "", m.group(1))

    # Email
    email = None
    m = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    if m:
        email = m.group(0)

    # Telefon (robust DK)
    phone = None
    m = re.search(r"(\+?\s*45\s*)?(\d[\d\s\-]{6,}\d)", text)
    if m:
        raw = re.sub(r"[^\d+]", "", m.group(0))
        phone = raw

    # Adresse (simpel DK-heuristik)
    address = None
    m = re.search(r"\b([A-ZÆØÅa-zæøå]+\s+\d+[A-Z]?)\s*,?\s*(\d{4})\s+([A-ZÆØÅa-zæøå]+)\b", text)
    if m:
        address = f"{m.group(1)}, {m.group(2)} {m.group(3)}"

    return {"cvr": cvr, "email": email, "phone": phone, "address": address}


# ------------------------------------------------------------
# Logic helpers
# ------------------------------------------------------------
def schema_find_org_like(objs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for o in objs:
        t = o.get("@type")
        ts = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
        ts = [str(x).lower() for x in ts]
        if any(x in ts for x in ["organization", "localbusiness", "corporation"]):
            return o
    return None

def schema_find_person(objs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for o in objs:
        t = o.get("@type")
        ts = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
        ts = [str(x).lower() for x in ts]
        if "person" in ts:
            return o
    return None

def detect_social_links(ext_links: List[str]) -> List[str]:
    social_domains = [
        "facebook.com", "instagram.com", "linkedin.com", "tiktok.com",
        "youtube.com", "x.com", "twitter.com", "trustpilot.com",
        "google.com/maps",
    ]
    return sorted({u for u in ext_links if any(d in u.lower() for d in social_domains)})

def count_external_citations(ext_links: List[str]) -> int:
    socials = set(detect_social_links(ext_links))
    cites = [u for u in ext_links if u not in socials]
    return len(set(cites))

def guess_page_type(
    title: str,
    headings: Dict[str, List[str]],
    text: str,
    url: str = "",
    schema_types: Optional[List[str]] = None,
) -> str:
    """Best-effort page type classifier.

    NOTE: This tool needs to distinguish at least:
      - Product Page (webshop PDP)
      - Service Page
      - Content / Article
      - General Page

    We use a blend of URL patterns, schema types (when available), and textual cues.
    """
    schema_types = schema_types or []
    norm_types = {norm_schema_type(t) for t in schema_types if t}

    t_low = (title or "").lower()
    h1 = " ".join(headings.get("h1", []) or []).lower()
    h2 = " ".join(headings.get("h2", []) or []).lower()
    hay = " ".join([t_low, h1, h2]).strip()

    url_low = (url or "").lower()

    # -----------------
    # 1) Product page
    # -----------------
    # Strong signals: Product/Offer schema OR URL looks like PDP OR typical PDP UI words.
    product_url_signals = [
        "/products/", "/product/", "?variant=", "/p/", "/item/", "/shop/",
    ]
    product_schema_signals = {"Product", "Offer", "AggregateRating", "Review", "ItemList"}
    product_text_terms = [
        "add to cart", "læg i kurv", "læg i indkøbskurv", "køb", "køb nu",
        "variant", "varianter", "størrelse", "farve", "lager", "på lager", "udsolgt",
        "levering", "fri fragt", "retur", "betaling", "pris", "kr", "dkk",
        "ingredients", "ingredienser", "anmeldelser", "ratings", "specifikation",
    ]

    has_product_schema = bool(norm_types.intersection(product_schema_signals))
    looks_like_product_url = any(s in url_low for s in product_url_signals)
    looks_like_product_text = any(w in hay for w in product_text_terms) or any(w in (text or "")[:2500].lower() for w in product_text_terms)

    # Product page if schema says so OR URL strongly suggests PDP OR text has strong PDP terms.
    if has_product_schema:
        return "Product Page"

    if looks_like_product_url:
        return "Product Page"

    if looks_like_product_text and any(s in url_low for s in ["/collections/", "/product", "/shop", "/p/"]):
        return "Product Page"

    # -----------------
    # 2) Service page
    # -----------------
    service_terms = [
        "service", "ydelse", "vi tilbyder", "pris", "tilbud", "bestil",
        "kontakt", "fliserens", "tagrens", "facaderens", "alge",
        "imprægner", "rengøring", "behandling", "rens", "terrasse",
    ]

    if any(t in hay for t in service_terms):
        return "Service Page"

    # -----------------
    # 3) Content/article
    # -----------------
    blog_terms = ["blog", "nyhed", "artikel", "guide", "sådan", "tips", "viden", "råd"]
    if any(t in hay for t in blog_terms):
        return "Content / Article"

    # -----------------
    # 4) Fallbacks
    # -----------------
    # If schema strongly indicates a product-ish entity but we missed the above, still classify as Product.
    if "Product" in norm_types or "Offer" in norm_types:
        return "Product Page"

    # Lightweight fallback on length
    if len((text or "")) < 1500:
        return "General Page"

    return "General Page"


def detect_unsourced_claims(text: str, ext_links: List[str]) -> List[str]:
    patterns = [
        r"\b100\%\b",
        r"\b\d+\s*års\b",
        r"\bgaranti\b",
        r"\bgodkendt\b",
        r"\bcertificer\w+\b",
        r"\bMiljøstyrels\w+\b",
        r"\bISO\s*\d+\b",
        r"\bEU\s*Ecolabel\b",
        r"\bSvanemærk\w+\b",
        r"\btest\w+\b",
        r"\blaborator\w+\b",
        r"\bmiljøvenlig\b",
    ]
    # Hvis vi har mange eksterne citations, antag at de kan være dokumenteret
    if count_external_citations(ext_links) > 1:
        return []
    matches: List[str] = []
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            matches.append(m.group(0))
    return list(set(matches))

# ------------------------------------------------------------
# Product extraction (for Product Page entity maps)
# ------------------------------------------------------------

def _safe_str(x: Any) -> str:
    try:
        return str(x).strip()
    except Exception:
        return ""

def _first_nonempty(*vals: Any) -> str:
    for v in vals:
        s = _safe_str(v)
        if s:
            return s
    return ""

def _as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]

def _schema_find_first(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj.get(key)
        for v in obj.values():
            found = _schema_find_first(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for it in obj:
            found = _schema_find_first(it, key)
            if found is not None:
                return found
    return None

def extract_product_signals(
    html: str,
    final_url: str,
    title: str,
    headings: Dict[str, List[str]],
    meta: Dict[str, str],
    schema_objs: List[Dict[str, Any]],
    text: str,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "product_name": "",
        "brand": "",
        "collection": "",
        "price": "",
        "currency": "",
        "availability": "",
        "sku": "",
        "offers_count": 0,
        "variants": [],
    }

    url_low = (final_url or "").lower()

    # Collection from URL (Shopify typical)
    m = re.search(r"/collections/([^/?#]+)", url_low)
    if m:
        out["collection"] = m.group(1).replace("-", " ").strip().title()

    # Find Product schema object
    product_obj: Optional[Dict[str, Any]] = None
    for o in schema_objs or []:
        t = o.get("@type")
        ts = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
        ts = [norm_schema_type(str(x)) for x in ts]
        if any(x.lower() == "product" for x in ts):
            product_obj = o
            break

    if product_obj:
        out["product_name"] = _first_nonempty(product_obj.get("name"), product_obj.get("headline"))

        b = product_obj.get("brand")
        if isinstance(b, dict):
            out["brand"] = _first_nonempty(b.get("name"), b.get("@id"))
        elif isinstance(b, str):
            out["brand"] = b.strip()

        out["sku"] = _first_nonempty(product_obj.get("sku"), product_obj.get("mpn"))

        offers_list = _as_list(product_obj.get("offers"))
        out["offers_count"] = len(offers_list)

        offer_summary: Optional[Dict[str, Any]] = None
        for off in offers_list:
            if isinstance(off, dict):
                offer_summary = off
                break

        if offer_summary:
            price = _first_nonempty(offer_summary.get("price"), _schema_find_first(offer_summary, "price"))
            currency = _first_nonempty(offer_summary.get("priceCurrency"), _schema_find_first(offer_summary, "priceCurrency"))
            availability = _first_nonempty(offer_summary.get("availability"), _schema_find_first(offer_summary, "availability"))

            out["price"] = price
            out["currency"] = currency

            if availability and "schema.org" in availability:
                out["availability"] = availability.rsplit("/", 1)[-1]
            else:
                out["availability"] = availability

        variants = product_obj.get("hasVariant") or product_obj.get("isVariantOf")
        for v in _as_list(variants):
            if isinstance(v, dict):
                nm = _first_nonempty(v.get("name"), v.get("sku"), v.get("mpn"))
                if nm:
                    out["variants"].append(nm)
            elif isinstance(v, str) and v.strip():
                out["variants"].append(v.strip())

    # DOM fallbacks
    if html:
        soup = BeautifulSoup(html, "html.parser")

        h1s = headings.get("h1", []) or []
        out["product_name"] = _first_nonempty(
            out.get("product_name"),
            meta.get("og:title"),
            (h1s[0] if h1s else ""),
            title,
        )

        og_site = (meta.get("og:site_name") or "").strip()
        if not out.get("brand") and og_site:
            out["brand"] = og_site

        if not out.get("price"):
            for sel in [
                ("meta", {"property": "product:price:amount"}, "content"),
                ("meta", {"property": "og:price:amount"}, "content"),
                ("meta", {"itemprop": "price"}, "content"),
                ("span", {"itemprop": "price"}, None),
            ]:
                tag = soup.find(sel[0], attrs=sel[1])
                if tag:
                    val = (tag.get(sel[2]) if sel[2] else tag.get_text(" ", strip=True))
                    if val:
                        out["price"] = str(val).strip()
                        break

        if not out.get("currency"):
            tag = soup.find("meta", attrs={"itemprop": "priceCurrency"})
            if tag and tag.get("content"):
                out["currency"] = tag.get("content").strip()

        if not out.get("availability"):
            snippet = (text or "")[:2500].lower()
            if "på lager" in snippet or "in stock" in snippet:
                out["availability"] = "InStock"
            elif "udsolgt" in snippet or "out of stock" in snippet:
                out["availability"] = "OutOfStock"

    out["variants"] = list(dict.fromkeys([v for v in out.get("variants", []) if v]))[:6]
    return out

# ------------------------------------------------------------
# Topic entity extraction (brands/products/etc.) for entity map
# ------------------------------------------------------------

# --- spaCy loader helper (module-level, before extract_topic_entities) ---
@st.cache_resource(show_spinner=False)
def _load_spacy_model():
    """Try DK model first, else multilingual fallback."""
    try:
        import spacy  # type: ignore
    except Exception:
        spacy = None
    if spacy is None:
        return None
    for model_name in ("da_core_news_sm", "xx_ent_wiki_sm"):
        try:
            return spacy.load(model_name)
        except Exception:
            continue
    return None

def extract_topic_entities(title: str, headings: Dict[str, List[str]], text: str, max_entities: int = 18) -> List[Tuple[str, int]]:
    """Extract topic entities for the entity map.

    Prefer real NER (spaCy) when available. Fall back to a lightweight heuristic
    (capitalized phrases) when spaCy isn't installed or a model isn't available.
    """
    h1 = " ".join(headings.get("h1", []) or [])
    h2 = " ".join(headings.get("h2", []) or [])
    h3 = " ".join(headings.get("h3", []) or [])

    # Use a weighted snippet rather than the entire page text (reduces nav/UI noise)
    text_snippet = (text or "")[:3500]
    weighted = (" ".join([title, title, h1, h1, h2, h2, h3]) + " " + text_snippet).strip()
    if not weighted:
        return []

    banned_exact = {
        "WebPage", "Organization", "LocalBusiness", "Service", "Service Offer",
        "Author", "Author (Text)", "FAQ", "GDPR", "Danmark", "Denmark",
    }
    banned_contains = {
        "cookie", "privatliv", "vilkår", "betingelser", "login", "konto", "nyhedsbrev",
        "menu", "navigation", "søge", "søg", "filtrer", "sorter", "vælg", "klik",
        "læs", "download", "book", "bestil", "kontakt",
        "i18n", "error", "fejl",
    }
    stopwords_lower = {
        # Pronouns / function words
        "vi", "jeg", "du", "i", "man", "den", "det", "de", "der", "som", "for", "med", "til", "på", "af", "om", "og", "men", "så",
        "en", "et", "eller", "at", "ikke", "kun", "også", "derfor", "altså",
        # Common UI / CTA words that often show up in menus
        "vælg", "læs", "se", "klik", "kontakt", "bestil", "book", "ring", "få", "tilbud", "udfyld",
        # Common weak verbs/adverbs that we do not want as entities
        "skal", "kan", "må", "ofte", "altid", "uanset", "dette", "vores", "din", "jeres",
        # Additional stopwords
        "på", "hvad", "hos", "her", "nu", "mere", "mindre", "bedste", "resultat", "til",
        # Common product-option / UI noise we don't want as entities
        "tilføj", "normal", "i18n", "error", "fejl",
    }

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip())

    def _is_noise(ent: str) -> bool:
        if not ent or len(ent) < 3:
            return True
        if ent in banned_exact:
            return True

        low = ent.lower().strip()

        # Hard-kill common UI/tech artifacts that slip through (seen on webshop PDPs)
        if "i18n" in low:
            return True
        if "error" in low or "fejl" in low:
            return True

        # Tokenize on whitespace for smarter stopword/noise filtering
        tokens = [t for t in re.split(r"\s+", low) if t]
        # If any token is a stopword-like UI word, treat short entities as noise
        if len(tokens) <= 2 and any(t in {"tilføj", "normal"} for t in tokens):
            return True
        if not tokens:
            return True

        # Remove exact stopwords
        if low in stopwords_lower:
            return True

        # Single-token entities must be reasonably long and not function-words
        if len(tokens) == 1:
            if len(tokens[0]) <= 3:
                return True
            if tokens[0] in stopwords_lower:
                return True

        # If the entity is short (1–2 tokens) and contains any stopword, treat as noise
        if len(tokens) <= 2 and any(t in stopwords_lower for t in tokens):
            return True

        # If 50%+ of tokens are stopwords, it's almost certainly not an entity
        sw_ratio = sum(1 for t in tokens if t in stopwords_lower) / max(1, len(tokens))
        if sw_ratio >= 0.5:
            return True

        if any(b in low for b in banned_contains):
            return True
        if re.fullmatch(r"[\d\s.,-]+", ent):  # prices / numbers
            return True

        # Ignore shouty ALLCAPS phrases (menus/headlines)
        letters = re.sub(r"[^A-Za-zÆØÅæøå]", "", ent)
        if letters and letters.isupper() and len(letters) >= 6:
            return True

        # Special: patterns like "FACADERENS Hvad" (ALLCAPS + stopword-like second token)
        raw_tokens = [t for t in re.split(r"\s+", ent.strip()) if t]
        if len(raw_tokens) >= 2:
            first_letters = re.sub(r"[^A-Za-zÆØÅæøå]", "", raw_tokens[0])
            second_low = raw_tokens[1].lower()
            if first_letters and first_letters.isupper() and len(first_letters) >= 5 and second_low in stopwords_lower:
                return True

        if ent.isupper() and len(ent) <= 4:  # EU/DK etc.
            return True
        return False

    # --- 1) spaCy NER path ---
    nlp = _load_spacy_model()
    if nlp is not None:
        try:
            doc = nlp(weighted)
            counts: Dict[str, int] = {}

            allowed_labels = {"ORG", "PERSON", "PRODUCT", "GPE", "LOC", "EVENT", "WORK_OF_ART"}

            for ent in doc.ents:
                label = getattr(ent, "label_", "")
                txt = _norm(ent.text)

                if label and label not in allowed_labels:
                    continue
                if _is_noise(txt):
                    continue
                if txt.lower() in stopwords_lower:
                    continue
                if len(txt) > 60:
                    continue

                counts[txt] = counts.get(txt, 0) + 1

            ranked = sorted(counts.items(), key=lambda x: (-x[1], -len(x[0]), x[0]))
            return ranked[:max_entities]
        except Exception:
            pass  # fall back

    # --- 2) Heuristic fallback ---
    phrase_re = re.compile(
        r"\b[A-ZÆØÅ][A-Za-zÆØÅæøå0-9&/+'\-]{2,}(?:\s+[A-ZÆØÅ][A-Za-zÆØÅæøå0-9&/+'\-]{2,}){0,3}\b"
    )

    counts: Dict[str, int] = {}
    for m in phrase_re.finditer(weighted):
        ent = _norm(m.group(0))
        if _is_noise(ent):
            continue
        if ent.lower() in stopwords_lower:
            continue
        counts[ent] = counts.get(ent, 0) + 1

    ranked = sorted(counts.items(), key=lambda x: (-x[1], -len(x[0]), x[0]))
    out: List[Tuple[str, int]] = []
    for ent, c in ranked:
        if len(out) >= max_entities:
            break

        # Prefer multi-word entities; single words must be strong
        if " " not in ent:
            if c <= 1:
                continue
            if len(ent) < 5:
                continue

        # Drop weak, short, one-off phrases
        if c <= 1 and len(ent) < 8:
            continue

        out.append((ent, c))
    return out

def quick_wins(findings: List[Finding], max_items: int = 6) -> List[Finding]:
    wins = [f for f in findings if f.impact >= 4 and f.effort_minutes <= 30]
    return wins[:max_items]


# ------------------------------------------------------------
# Visualizations
# ------------------------------------------------------------
def render_donut_score(score: float, max_score: float = 10.0) -> None:
    val = float(score)
    val = max(0.0, min(max_score, val))
    remaining = max_score - val

    fig, ax = plt.subplots(figsize=(2, 2), dpi=150)
    fig.patch.set_alpha(0.0)
    ax.patch.set_alpha(0.0)

        # Farve efter score
    if val < 4.0:
        main_color = "#ef4444"  # rød
    elif val <= 7.0:
        main_color = "#eab308"  # gul
    else:
        main_color = "#22c55e"  # grøn

    colors = [main_color, "#e2e8f0"]

    ax.pie(
        [val, remaining],
        startangle=90,
        counterclock=False,
        colors=colors,
        wedgeprops={"width": 0.25, "edgecolor": "none"},
    )

    ax.text(0, 0.1, f"{val:.1f}", ha="center", va="center", fontsize=20, fontweight="bold", color="#1e293b")
    ax.text(0, -0.25, "Score", ha="center", va="center", fontsize=10, color="#64748b")

    ax.set(aspect="equal")
    ax.set_axis_off()
    st.pyplot(fig, clear_figure=True, use_container_width=False)

def render_graphviz_map(payload: Dict[str, Any]):
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])

    graph = graphviz.Digraph()
    graph.attr(rankdir="LR", size="8,5", bgcolor="transparent")
    graph.attr("node", shape="box", style="rounded,filled", fillcolor="white", color="#cbd5e1", fontname="Helvetica")
    graph.attr("edge", color="#94a3b8", fontname="Helvetica", fontsize="10")

    for n in nodes:
        nid = n.get("id")
        style = n.get("style", "rounded,filled")
        fill = n.get("color", "#f8fafc")
        fontcolor = "#dc2626" if "dashed" in style else "#1e293b"
        bordercolor = "#dc2626" if "dashed" in style else "#cbd5e1"
        label = f"{n.get('label', nid)}\n({n.get('type', '')})"
        graph.node(nid, label=label, style=style, fillcolor=fill, color=bordercolor, fontcolor=fontcolor)

    for e in edges:
        src = e.get("from")
        dst = e.get("to")
        if not src or not dst:
            continue

        rel = e.get("rel", "")
        style = e.get("style", "solid")

        if style == "missing":
            edge_style = "dashed"
            color = "#ef4444"
        elif style in ("weak", "dashed"):
            edge_style = "dashed"
            color = "#94a3b8"
        else:
            edge_style = "solid"
            color = "#94a3b8"

        graph.edge(str(src), str(dst), label=str(rel), style=edge_style, color=color, fontcolor=color)

    st.graphviz_chart(graph, use_container_width=True)

def render_entity_map(payload: Dict[str, Any], height_px: int = 700) -> None:
    """Interactive entity map (PyVis/vis.js) with Graphviz fallback."""
    if Network is None:
        st.info("Interaktiv graf kræver 'pyvis'. Viser fallback-graf i stedet.")
        render_graphviz_map(payload)
        return

    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])

    net = Network(height=f"{height_px}px", width="100%", directed=True, bgcolor="#ffffff")

    # Physics tuned for readable layout
    net.barnes_hut(
        gravity=-9000,
        central_gravity=0.25,
        spring_length=160,
        spring_strength=0.02,
        damping=0.35,
        overlap=0.4,
    )

    # NOTE: PyVis expects a JSON string here (not JS like `var options = {...}`)
    options = {
        "nodes": {
            "shape": "dot",
            "borderWidth": 2,
            "font": {"size": 14, "face": "Helvetica", "color": "#0f172a"},
        },
        "edges": {
            "color": {"color": "#94a3b8"},
            "smooth": {"type": "dynamic"},
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.7}},
            "font": {"size": 12, "color": "#64748b", "align": "middle"},
        },
        "physics": {"enabled": True},
        "interaction": {
            "hover": True,
            "dragNodes": True,
            "dragView": True,
            "zoomView": True,
        },
    }
    net.set_options(json.dumps(options))

    def infer_group(ntype: str, nid: str) -> str:
        t = (ntype or "").lower()
        if "person" in t or "author" in t:
            return "author"
        if "organization" in t or "localbusiness" in t or nid == "org":
            return "org"
        if "product" in t or nid == "product":
            return "product"
        if "brand" in t or nid == "brand":
            return "brand"
        if "collection" in t or nid == "collection":
            return "collection"
        if "offer" in t or nid == "offer":
            return "offer"
        if "price" in t or nid == "price":
            return "price"
        if "availability" in t or nid == "availability":
            return "availability"
        if "variant" in t or nid.startswith("variant_"):
            return "variant"
        if "service" in t:
            return "service"
        if "cited" in t:
            return "cited"
        if "missing" in t or nid.startswith("miss_"):
            return "missing"
        if "page" in t or nid == "page":
            return "page"
        return "topic"

    group_colors = {
        "author": ("#3b82f6", "#3b82f6"),
        "org": ("#7c3aed", "#7c3aed"),
        "product": ("#10b981", "#10b981"),
        "brand": ("#60a5fa", "#60a5fa"),
        "collection": ("#a78bfa", "#a78bfa"),
        "offer": ("#f59e0b", "#f59e0b"),
        "price": ("#fb923c", "#fb923c"),
        "availability": ("#94a3b8", "#94a3b8"),
        "variant": ("#22c55e", "#22c55e"),
        "topic": ("#22c55e", "#22c55e"),
        "cited": ("#f59e0b", "#f59e0b"),
        "missing": ("#e5e7eb", "#ef4444"),
        "service": ("#10b981", "#10b981"),
        "page": ("#e2e8f0", "#94a3b8"),
    }

    for n in nodes:
        nid = n.get("id")
        if not nid:
            continue

        label = n.get("label", nid)
        ntype = n.get("type", "")
        style = n.get("style", "")
        group = n.get("group") or infer_group(str(ntype), str(nid))

        fill, border = group_colors.get(group, ("#e2e8f0", "#94a3b8"))

        is_missing = ("dashed" in str(style)) or (group == "missing")
        if is_missing:
            border = "#ef4444"
            font_color = "#ef4444"
        else:
            font_color = "#0f172a"

        default_size = 18
        if group in ("product",):
            default_size = 38
        elif group in ("org", "service"):
            default_size = 28
        elif group in ("brand", "collection"):
            default_size = 24
        elif group in ("offer",):
            default_size = 22
        elif group in ("author",):
            default_size = 22
        elif group in ("cited",):
            default_size = 20
        elif group in ("page",):
            default_size = 24
        elif group in ("price", "availability"):
            default_size = 18
        elif group in ("variant",):
            default_size = 16
        elif group in ("missing",):
            default_size = 22
        elif group in ("topic",):
            default_size = 22

        size = int(n.get("size", default_size))
        title = f"{label}<br><span style='color:#64748b'>({ntype})</span>"

        net.add_node(
            nid,
            label=label,
            title=title,
            size=size,
            color={"background": fill, "border": border},
            font={"color": font_color},
        )

    for e in edges:
        src = e.get("from")
        dst = e.get("to")
        if not src or not dst:
            continue

        rel = e.get("rel", "")
        style = e.get("style", "solid")
        dashed = (style == "dashed")
        color = "#ef4444" if dashed else "#94a3b8"
        net.add_edge(src, dst, label=rel, dashes=dashed, color=color)

    legend_html = """
<div style="font-family: Helvetica, Arial, sans-serif; padding: 12px 6px 10px 6px;">
  <div style="font-size:18px; font-weight:700; color:#0f172a;">Entity Relationship Map</div>
  <div style="color:#64748b; margin-top:2px;">Visual representation of entities AI sees in your content</div>

  <div style="display:flex; gap:18px; flex-wrap:wrap; margin-top:12px; align-items:center;">
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="width:10px; height:10px; border-radius:999px; background:#3b82f6; display:inline-block;"></span> Author
    </div>
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="width:10px; height:10px; border-radius:999px; background:#7c3aed; display:inline-block;"></span> Organization
    </div>
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="width:10px; height:10px; border-radius:999px; background:#22c55e; display:inline-block;"></span> Topic Entity
    </div>
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="width:10px; height:10px; border-radius:999px; background:#f59e0b; display:inline-block;"></span> Cited Entity
    </div>
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="width:10px; height:10px; border-radius:999px; background:#e5e7eb; border:2px solid #ef4444; display:inline-block;"></span> Missing Entity
    </div>
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="width:10px; height:10px; border-radius:999px; border:2px solid #22c55e; display:inline-block;"></span> ✓ Has Markup
    </div>
    <div style="display:flex; align-items:center; gap:8px;">
      <span style="width:10px; height:10px; border-radius:999px; border:2px solid #ef4444; display:inline-block;"></span> ⚠ Not Recognized
    </div>
  </div>
</div>
"""

    html = net.generate_html()
    html = html.replace("<body>", "<body>" + legend_html, 1)
    components.html(html, height=height_px + 160, scrolling=False)

def schema_snippet_suggestions(page_type: str) -> Dict[str, str]:
    org = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "[Virksomhedsnavn]",
        "url": "[https://eksempel.dk]",
        "logo": "[https://eksempel.dk/logo.png]",
        "telephone": "[+45 xx xx xx xx]",
        "address": {"@type": "PostalAddress", "addressCountry": "DK"},
        "sameAs": ["[Facebook URL]", "[LinkedIn URL]", "[Trustpilot URL]"],
    }
    local = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "@id": "[https://eksempel.dk/#organization]",
        "name": "[Virksomhedsnavn]",
        "areaServed": {"@type": "Country", "name": "Denmark"},
        "priceRange": "$$",
        "telephone": "[+45 xx xx xx xx]",
    }
    service = {
        "@context": "https://schema.org",
        "@type": "Service",
        "serviceType": "[Fliserens / Tagrens / ...]",
        "provider": {"@id": "[https://eksempel.dk/#organization]"},
        "areaServed": {"@type": "Country", "name": "Denmark"},
    }
    person = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": "[Navn]",
        "jobTitle": "[Rolle]",
        "worksFor": {"@id": "[https://eksempel.dk/#organization]"},
        "sameAs": ["[LinkedIn URL]"],
    }

    webpage = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": "[Sidens titel]",
        "url": "[https://eksempel.dk/denne-side]",
        "isPartOf": {"@id": "[https://eksempel.dk/#website]"},
        "about": "[Kort emne/ydelse]",
        "primaryImageOfPage": "[https://eksempel.dk/billede.jpg]",
    }

    creativework = {
        "@context": "https://schema.org",
        "@type": "CreativeWork",
        "headline": "[Titel]",
        "description": "[Kort beskrivelse]",
        "url": "[https://eksempel.dk/denne-side]",
        "datePublished": "2025-01-01",
        "dateModified": "2025-01-01",
        "author": {"@type": "Person", "name": "[Navn]"},
        "publisher": {"@type": "Organization", "name": "[Virksomhedsnavn]"},
    }

    sitenav = {
        "@context": "https://schema.org",
        "@type": "SiteNavigationElement",
        "name": ["[Menu punkt 1]", "[Menu punkt 2]", "[Menu punkt 3]"],
        "url": ["[https://eksempel.dk/side-1]", "[https://eksempel.dk/side-2]", "[https://eksempel.dk/side-3]"]
    }

    out = {
        "Organization": json.dumps(org, ensure_ascii=False, indent=2),
        "LocalBusiness": json.dumps(local, ensure_ascii=False, indent=2),
        "Person": json.dumps(person, ensure_ascii=False, indent=2),
        "WebPage": json.dumps(webpage, ensure_ascii=False, indent=2),
        "CreativeWork": json.dumps(creativework, ensure_ascii=False, indent=2),
        "SiteNavigationElement": json.dumps(sitenav, ensure_ascii=False, indent=2),
    }
    if page_type == "Service Page":
        out["Service"] = json.dumps(service, ensure_ascii=False, indent=2)

    # Keep suggestions relevant-ish
    if page_type == "Content / Article":
        # CreativeWork is relevant; WebPage can stay
        pass
    elif page_type == "Service Page":
        # Service is already included above; keep WebPage as optional
        pass
    return out


# ------------------------------------------------------------
# CORE LOGIC
# ------------------------------------------------------------
def score_and_findings(
    page_type: str,
    title: str,
    text: str,
    headings: Dict[str, List[str]],
    schema_types: List[str],
    schema_objs: List[Dict[str, Any]],
    internal_links: List[str],
    ext_links: List[str],
    meta: Dict[str, str],
    nap: Dict[str, Optional[str]],
    indexability: Dict[str, Any],
    final_url: str = "",
    raw_html: str = "",
) -> Tuple[float, float, float, float, List[Finding], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    findings: List[Finding] = []

    # --- 1) Base signals ---
    org_obj = schema_find_org_like(schema_objs)
    person_obj = schema_find_person(schema_objs)
    socials = detect_social_links(ext_links)

    internal_join = " ".join(internal_links).lower()
    has_about = any(k in internal_join for k in ["/om", "about", "om-os", "about-us"])
    has_contact = any(k in internal_join for k in ["/kontakt", "contact"])
    has_privacy = any(k in internal_join for k in ["/privacy", "privatliv", "cookie", "cookies", "gdpr"])

    external_citations = count_external_citations(ext_links)
    unsourced_claims = detect_unsourced_claims(text, ext_links)

    author_visible = bool(re.search(r"\b(forfatter|skrevet af|author|by)\b", text, re.I))

    clean_schema_types = [norm_schema_type(t) for t in schema_types]
    has_webpage_schema = ("WebPage" in clean_schema_types)
    has_creativework_schema = ("CreativeWork" in clean_schema_types) or ("Article" in clean_schema_types) or ("BlogPosting" in clean_schema_types)
    has_sitenav_schema = ("SiteNavigationElement" in clean_schema_types)

    raw_html_low = (raw_html or "").lower()
    has_nav_dom = ("<nav" in raw_html_low) or ("role=\"navigation\"" in raw_html_low) or ("role='navigation'" in raw_html_low)
    has_review_schema = ("Review" in clean_schema_types) or ("AggregateRating" in clean_schema_types)
    reviews_mentioned = ("trustpilot" in text.lower()) or ("anmeldelse" in text.lower()) or ("stjerner" in text.lower())

    has_h1 = len(headings.get("h1", [])) > 0
    h2_count = len(headings.get("h2", []))

    # --- 2) New “page reality” signals ---
    word_count = len(text.split())
    meta_desc = (meta.get("description") or "").strip()
    has_canonical = bool((meta.get("canonical") or "").strip())

    # Indexability (hard gate for Technical)
    is_blocked = bool((indexability or {}).get("blocked"))
    index_label = (indexability or {}).get("label") or "Uncertain"
    index_reasons = ", ".join((indexability or {}).get("blocked_reasons") or [])

    # Pricing / process / area / faq / before-after / contact CTA
    has_pricing = bool(re.search(r"\b(pris|priser|fra\s+\d+|kr\.?|dkk)\b", text, re.I))
    has_process = bool(re.search(r"\b(sådan\s+foregår|proces|trin\s+\d|step\s+\d|fremgangsmåde)\b", text, re.I))
    has_service_area = bool(re.search(r"\b(vi\s+kører|dækker|område|hele\s+danmark|sjælland|jylland|fyn|københavn|aarhus)\b", text, re.I))
    has_before_after = bool(re.search(r"\b(før\s+og\s+efter|before/after)\b", text, re.I))
    has_faq_like = bool(re.search(r"\b(spørgsmål|faq|ofte\s+stillede)\b", text, re.I)) or any("?" in h for h in headings.get("h2", []))

    has_contact_cta = bool(re.search(r"\b(kontakt\s+os|ring\s+nu|få\s+tilbud|book|bestil)\b", text, re.I))

    # Guarantee: only “credible” if terms/conditions exist
    has_guarantee = bool(re.search(r"\bgaranti\b", text, re.I))
    has_guarantee_years = bool(re.search(r"\b\d+\s*års\s*garanti\b", text, re.I))
    has_terms = bool(re.search(r"\b(gælder|forudsætter|vilkår|betingelser|undtaget|dokumentation)\b", text, re.I))

    # -----------------
    # USP detection (service-focused)
    # -----------------
    def detect_usps(txt: str) -> Dict[str, Any]:
        t = (txt or "")
        tl = t.lower()

        years = None
        m = re.search(r"\b(\d{1,2})\s*\+?\s*(?:års?|aar)\s+(?:erfaring|brancheerfaring)\b", tl)
        if m:
            try:
                years = int(m.group(1))
            except Exception:
                years = None

        specialist = bool(re.search(r"\b(specialist(?:er)?\s+i|specialiseret\s+i|ekspert(?:er)?\s+i)\b", tl))
        authorization = bool(re.search(r"\b(autoriseret|certificeret|godkendt\s+værksted|vvs-autoriseret|el-autoriseret)\b", tl))
        awards = bool(re.search(r"\b(prisvindende|award|udmærkelse|kåret\s+som|vinder\s+af)\b", tl))

        # Review/star claims (on-page text)
        star_claim = bool(re.search(r"\b(\d(?:[\.,]\d)?\s*/\s*5|\d(?:[\.,]\d)?\s*ud\s*af\s*5|5\s*-?\s*stjern(?:er|ede)|\d\s*stjerner)\b", tl))

        # Guarantee as USP (separate from 'terms')
        guarantee_claim = has_guarantee

        usp_flags = {
            "years_experience": years,
            "has_specialist": specialist,
            "has_authorization": authorization,
            "has_awards": awards,
            "has_star_claim": star_claim,
            "has_guarantee": guarantee_claim,
        }
        usp_count = sum(
            1
            for k, v in usp_flags.items()
            if (k == "years_experience" and isinstance(v, int) and v >= 5) or (k != "years_experience" and bool(v))
        )
        usp_flags["usp_count"] = usp_count
        return usp_flags

    usps = detect_usps(text)
    usp_count = int(usps.get("usp_count") or 0)

    # Evidence links quality (simple)
    high_trust_domains = ("mst.dk", "miljo", "miljø", "ds.dk", "iso.org", "ecolabel", "svanemaerket", "svanemærket", "sikkerhedsdatablad", "sds")
    trusted_out_links = [u for u in ext_links if any(k in u.lower() for k in high_trust_domains)]

    # -----------------
    # Review platform signals (Trustpilot vs Google)
    # -----------------
    ext_low = [u.lower() for u in (ext_links or [])]
    has_trustpilot_link = any("trustpilot" in u for u in ext_low)
    has_google_reviews_link = any(
        ("google.com/maps" in u) or ("g.page" in u) or ("googleusercontent" in u)
        for u in ext_low
    )

    # Schema review signals already tracked via Review/AggregateRating
    has_review_platform_signal = bool(has_trustpilot_link or has_google_reviews_link or has_review_schema or reviews_mentioned)

    # Expert quotes / attribution (simple heuristic)
    # Used by requirements + findings. We treat either explicit attribution language
    # or at least one high-trust external link as a positive signal.
    has_expert_quotes = bool(
        re.search(
            r"\b(ifølge|kilde\s*:|source\s*:|referenc\w*|rapport|studie|undersøgelse|data\s+fra|SDS|sikkerhedsdatablad|standard|myndighed)\b",
            text or "",
            re.I,
        )
    ) or (len(trusted_out_links) >= 1)

    # NAP presence
    nap_phone = bool(nap.get("phone"))
    nap_email = bool(nap.get("email"))
    nap_address = bool(nap.get("address"))
    nap_cvr = bool(nap.get("cvr"))

    # --- 3) Scoring (single source of truth: score = 10 - missing impact) ---

    def _req(pillar: str, label: str, ok: bool, detail: str, impact_points: float):
        return {
            "pillar": pillar,
            "label": label,
            "ok": bool(ok),
            "detail": detail,
            "impact_points": float(impact_points),
        }

    # Build requirements (these MUST match what we show as prioritized actions)
    requirements = {
        "Entity Authority": [
            _req(
                "Entity Authority",
                "Business entity schema (Organization eller LocalBusiness)",
                bool(org_obj) or ("Organization" in clean_schema_types) or ("LocalBusiness" in clean_schema_types),
                "Tilføj Organization/LocalBusiness JSON-LD med navn, url, logo, kontakt, sameAs.",
                3.0,
            ),
            _req(
                "Entity Authority",
                "Kontaktinfo synlig (telefon eller email)",
                bool(nap_phone or nap_email),
                "Vis telefon/email tydeligt (fx footer/kontaktsektion) og gerne i schema.",
                1.0,
            ),
            _req(
                "Entity Authority",
                "Adresse/servicebase synlig",
                bool(nap_address),
                "Tilføj adresse eller tydelig base + serviceområde.",
                1.0,
            ),
            _req(
                "Entity Authority",
                "CVR synlig",
                bool(nap_cvr),
                "Vis CVR i footer/kontakt (og gerne i schema).",
                1.0,
            ),
            _req(
                "Entity Authority",
                "Min. 2 sociale profiler / sameAs links",
                len(socials) >= 2,
                "Tilføj Facebook/LinkedIn/Instagram + evt. Trustpilot i sameAs.",
                1.5,
            ),
            _req(
                "Entity Authority",
                "Om os-side findes (internt link)",
                bool(has_about),
                "Tilføj eller link til /om, /om-os, /about-us.",
                0.8,
            ),
            _req(
                "Entity Authority",
                "Kontakt-side findes (internt link)",
                bool(has_contact),
                "Tilføj eller link til /kontakt.",
                0.8,
            ),
            _req(
                "Entity Authority",
                "Forfatter/Person attribution (artikler/guides)",
                (bool(person_obj or author_visible) if page_type != "Service Page" else True),
                "Tilføj forfatterboks + Person schema (navn, rolle, credentials, sameAs).",
                2.5,
            ),
        ],
        "Content Credibility": [
            _req(
                "Content Credibility",
                "Mindst 1 trusted kilde (myndighed/standard/datablad)",
                len(trusted_out_links) >= 1,
                "Link til fx myndighed, standard, SDS/datablad, miljømærke, producentdokumentation.",
                1.5,
            ),
            _req(
                "Content Credibility",
                "Ekspertudtalelse eller tydelig kilde-attribution",
                bool(has_expert_quotes),
                "Tilføj 1–2 korte citater/udtalelser med attribution + link til kilde.",
                1.0,
            ),
            _req(
                "Content Credibility",
                "Tilstrækkeligt indhold (≥ 450 ord)",
                word_count >= 450,
                "Udbyg med FAQ, metode, materialer, garanti/vilkår, cases, serviceområde.",
                1.5,
            ),
            _req(
                "Content Credibility",
                "Proces/arbejdsgang (service-sider)",
                (bool(has_process) if page_type == "Service Page" else True),
                "Beskriv 3–6 trin (forberedelse → udførelse → efterbehandling).",
                1.0,
            ),
            _req(
                "Content Credibility",
                "Pris-/fra-pris signal (service-sider)",
                (bool(has_pricing) if page_type == "Service Page" else True),
                "Tilføj fra-pris, priseksempler eller hvad der påvirker prisen.",
                0.8,
            ),
            _req(
                "Content Credibility",
                "USP'er tydelige (min. 2 stærke USP-signaler)",
                (usp_count >= 2 if page_type == "Service Page" else True),
                "Tilføj en kort USP-blok (fx '+15 års erfaring', 'Autoriseret', 'Specialister i X', '5-stjernede anmeldelser', 'Garanti') tæt på hero/CTA.",
                1.2,
            ),
            _req(
                "Content Credibility",
                "Anmeldelser signal (Trustpilot/Google eller schema)",
                (has_review_platform_signal if page_type == "Service Page" else True),
                "Vis anmeldelser (Trustpilot/Google) med link eller strukturer dem (AggregateRating/Review).",
                1.0,
            ),
            _req(
                "Content Credibility",
                "Serviceområde tydeligt (service-sider)",
                (bool(has_service_area) if page_type == "Service Page" else True),
                "Tilføj byer/regioner eller 'Hele Danmark' + evt. liste over områder.",
                0.7,
            ),
        ],
        "Technical Signals": [
            _req(
                "Technical Signals",
                "Schema markup findes (mindst 1 type)",
                bool(clean_schema_types),
                "Tilføj mindst business entity + relevant side-type schema.",
                2.0,
            ),
            _req(
                "Technical Signals",
                "WebPage schema (grundmarkup)",
                bool(has_webpage_schema),
                "Tilføj WebPage JSON-LD (name, url, isPartOf) for at gøre sidetypen tydelig for AI.",
                0.7,
            ),
            _req(
                "Technical Signals",
                "CreativeWork/Article schema (artikler/guides)",
                (bool(has_creativework_schema) if page_type == "Content / Article" else True),
                "Tilføj CreativeWork/Article JSON-LD med headline, author, publisher, datoer.",
                0.9,
            ),
            _req(
                "Technical Signals",
                "SiteNavigationElement schema (når navigation findes)",
                (bool(has_sitenav_schema) if has_nav_dom else True),
                "Tilføj SiteNavigationElement JSON-LD for at gøre hovednavigationen maskinlæsbar.",
                0.5,
            ),
            _req(
                "Technical Signals",
                "Service schema (service-sider)",
                (bool("Service" in clean_schema_types) if page_type == "Service Page" else True),
                "Tilføj Service JSON-LD pr. ydelse og link provider til Organization/@id.",
                3.0,
            ),
            _req(
                "Technical Signals",
                "FAQPage schema når FAQ-indhold findes",
                (bool("FAQPage" in clean_schema_types) if has_faq_like else True),
                "Markér Q&A som FAQPage schema.",
                0.8,
            ),
            _req(
                "Technical Signals",
                "Canonical link findes",
                bool(has_canonical),
                "Tilføj rel=canonical.",
                0.6,
            ),
            _req(
                "Technical Signals",
                "Meta description findes",
                bool(meta_desc),
                "Tilføj unik meta description (140–160 tegn).",
                0.4,
            ),
            _req(
                "Technical Signals",
                "Privacy/cookie-link findes (internt link)",
                bool(has_privacy),
                "Tilføj link til cookie-/privatlivspolitik i footer.",
                0.8,
            ),
            # Removed: Indexability OK (ikke noindex/robots/HTTP-fejl)
        ],
        "Indexability": [
            _req(
                "Indexability",
                "Ingen noindex (meta/X-Robots-Tag)",
                ("noindex" not in parse_robots_directives((meta.get("robots") or "")))
                and ("noindex" not in parse_robots_directives((indexability.get("x_robots_tag") or ""))),
                "Fjern noindex fra meta robots eller X-Robots-Tag.",
                3.0,
            ),
            _req(
                "Indexability",
                "Robots.txt tillader siden",
                (indexability.get("robots_txt_allows") is not False),
                "Tjek robots.txt (User-agent: *) og fjern disallow for denne URL.",
                1.5,
            ),
            _req(
                "Indexability",
                "URL svarer 200 og er ikke blokeret",
                ((indexability.get("status") or 0) in (200, 201, 202)) and (not bool((indexability or {}).get("blocked"))),
                "Sørg for at siden svarer 200 og ikke er blokeret af robots/noindex.",
                3.0,
            ),
        ],
    }

    def _pillar_score(reqs) -> float:
        missing_points = sum(r["impact_points"] for r in reqs if not r["ok"])
        return clamp(10.0 - missing_points, 0.0, 10.0)

    entity_score = _pillar_score(requirements["Entity Authority"])
    cred_score   = _pillar_score(requirements["Content Credibility"])
    tech_score   = _pillar_score(requirements["Technical Signals"])
    index_score  = _pillar_score(requirements["Indexability"])

    # Overall: keep pillars visible and avoid "Indexability" being counted twice.
    overall = round(0.30 * entity_score + 0.30 * cred_score + 0.25 * tech_score + 0.15 * index_score, 1)

    # --- 4) Findings (more specific, less repetitive) ---
    # INDEXABILITY (highest priority)
    if is_blocked:
        reasons_txt = index_reasons or "Unknown"
        findings.append(
            Finding(
                "Technical Signals",
                "Critical",
                "Siden kan ikke indekseres (indexability issue)",
                "Hvis siden er noindex/blokeret eller ikke kan hentes stabilt, er alt andet sekundært.",
                "Fjern noindex (meta/X-Robots-Tag), ret robots.txt-regler, og sørg for at URL svarer 200.",
                5,
                10,
                evidence=f"Indexability: {index_label} • {reasons_txt}",
            )
        )
    # ENTITY
    if not org_obj and not (nap_phone or nap_email):
        sev = "Critical" if page_type == "Service Page" else "High"
        findings.append(Finding(
            "Entity Authority", sev,
            "Manglende virksomhedsidentitet (Organization + kontakt)",
            "AI kan ikke tydeligt forstå hvem der står bag siden, når både struktureret org og tydelige kontaktdata mangler.",
            "Tilføj Organization/LocalBusiness schema + tydelig kontaktblok (telefon/email) på siden.",
            5, 25,
            evidence="Ingen Organization/LocalBusiness i schema, og ingen tydelige kontaktdata fundet i NAP.",
            snippet=json.dumps({
                "@context": "https://schema.org",
                "@type": "LocalBusiness",
                "name": "Virksomhedsnavn",
                "telephone": "+45 xx xx xx xx",
                "email": "kontakt@eksempel.dk",
                "address": {"@type": "PostalAddress", "addressCountry": "DK"},
                "sameAs": ["https://dk.trustpilot.com/review/...", "https://www.facebook.com/..."]
            }, ensure_ascii=False, indent=2)
        ))

    if not nap_cvr:
        findings.append(Finding(
            "Entity Authority", "Medium",
            "CVR-nummer ikke fundet",
            "CVR er et stærkt DK-trustsignal og gør virksomheden let at verificere.",
            "Vis CVR i footer/kontaktsektion (og gerne i Organization schema).",
            3, 10,
            evidence="Ingen 'CVR' + 8 cifre fundet i HTML-tekst."
        ))

    if page_type == "Service Page" and not nap_address:
        findings.append(Finding(
            "Entity Authority", "High",
            "Adresse/servicebase ikke fundet",
            "For services er NAP og geografi en stor del af tillid og lokal relevans.",
            "Tilføj adresse eller tydelig 'base' + serviceområde (fx 'Hele Danmark / Sjælland').",
            4, 15,
            evidence="Ingen adresse-mønster fundet i HTML-tekst."
        ))

    if len(socials) == 0:
        findings.append(Finding(
            "Entity Authority", "High",
            "Ingen sameAs / sociale profiler linket",
            "AI bruger sociale profiler til at krydsvalidere at virksomheden er ægte og aktiv.",
            "Tilføj sameAs i Organization schema (Facebook/LinkedIn/Instagram + Trustpilot hvis relevant).",
            4, 15,
            evidence="Ingen social links fundet blandt eksterne links.",
            snippet='"sameAs": ["https://www.facebook.com/dinside", "https://www.linkedin.com/company/dinside", "https://dk.trustpilot.com/review/..."]'
        ))

    if not (person_obj or author_visible) and page_type != "Service Page":
        findings.append(Finding(
            "Entity Authority", "High",
            "Ingen forfatter/Person-attribution fundet",
            "Indhold fremstår anonymt for AI. På artikler/guides er afsenderkritisk (E-E-A-T).",
            "Tilføj forfatterboks + Person schema (navn, rolle, credentials, sameAs).",
            4, 25,
            evidence="Ingen Person schema og ingen 'skrevet af/author' signal fundet.",
            snippet=json.dumps({
                "@context": "https://schema.org",
                "@type": "Person",
                "name": "Navn Efternavn",
                "jobTitle": "Rolle",
                "worksFor": {"@type": "Organization", "name": "Virksomhedsnavn"},
                "sameAs": ["https://www.linkedin.com/in/..."]
            }, ensure_ascii=False, indent=2)
        ))

    # CREDIBILITY
    if unsourced_claims and external_citations == 0:
        claim_words = ", ".join(unsourced_claims[:5])
        findings.append(Finding(
            "Content Credibility", "Critical",
            "Udokumenterede påstande (claims) uden kilder",
            f"Ord som '{claim_words}' kræver dokumentation for at AI stoler på det.",
            "Tilføj links til certifikater, datablade, myndigheder eller tests (gerne fra højt-trust domæner).",
            5, 30,
            evidence=f"Claims fundet i tekst, men 0 eksterne citations: {claim_words}"
        ))

    if has_guarantee and not has_terms:
        findings.append(Finding(
            "Content Credibility", "Medium",
            "Garanti nævnt uden vilkår/betingelser",
            "Garanti uden betingelser ligner marketingclaim og kan skade troværdighed.",
            "Tilføj vilkår (hvad gælder det, hvad er undtaget, hvordan dokumenteres det) + evt. link til garanti-side.",
            3, 20,
            evidence="Garanti fundet, men ingen vilkårs-ord (gælder/forudsætter/vilkår/betingelser/undtaget) fundet."
        ))


    if page_type == "Service Page" and not has_before_after:
        findings.append(Finding(
            "Content Credibility", "Low",
            "Ingen 'før/efter' eller målbar dokumentation",
            "Cases og før/efter gør effekten konkret og øger trust.",
            "Tilføj 1–3 cases med billeder eller målbar effekt (fx algegrad, farve, holdbarhed).",
            2, 30,
            evidence="Ingen 'før og efter' signal fundet."
        ))




    if page_type == "Service Page" and not has_contact_cta:
        findings.append(Finding(
            "Content Credibility", "Low",
            "Svag eller manglende kontakt-CTA",
            "Hvis siden ikke tydeligt fortæller næste skridt, bliver den sværere at tolke som 'købbar service'.",
            "Tilføj tydelig CTA: 'Få tilbud', 'Ring', 'Book', 'Kontakt os' + response time.",
            2, 15,
            evidence="Ingen CTA-ord fundet (kontakt os/få tilbud/book/bestil)."
        ))

    # TECHNICAL

    if reviews_mentioned and not has_review_schema:
        findings.append(Finding(
            "Technical Signals", "High",
            "Reviews nævnes men ingen schema",
            "Du viser anmeldelser til mennesker, men gør dem svære for AI at aflæse struktureret.",
            "Tilføj AggregateRating eller Review schema (og match med reelle tal).",
            4, 45,
            evidence="Trustpilot/anmeldelse/stjerner nævnt i tekst, men Review/AggregateRating ikke fundet i schema.",
            snippet=json.dumps({
                "@context": "https://schema.org",
                "@type": "AggregateRating",
                "ratingValue": "4.8",
                "reviewCount": "150",
                "itemReviewed": {"@type": "LocalBusiness", "name": "Firma"}
            }, ensure_ascii=False, indent=2)
        ))

    # Sortering
    sev_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    findings.sort(key=lambda f: (sev_rank.get(f.severity, 9), -f.impact, f.effort_minutes))

    # De-dup findings (avoid repeated/overlapping titles)
    seen = set()
    deduped: List[Finding] = []
    for f in findings:
        key = (f.pillar.strip().lower(), f.title.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    findings = deduped

    # --- 5) Entity map ---
    nodes: List[Dict[str, Any]] = [{"id": "page", "label": "WebPage", "type": "Page", "color": "#e2e8f0"}]
    edges: List[Dict[str, Any]] = []
    product_signals = {}
    try:
        product_signals = extract_product_signals(
            html=raw_html or "",
            final_url=final_url or (meta.get("og:url") or ""),
            title=title,
            headings=headings,
            meta=meta,
            schema_objs=schema_objs,
            text=text,
        )
    except Exception:
        product_signals = {}

    if org_obj:
        name = str(org_obj.get("name") or "Organization")
        nodes.append({"id": "org", "label": name, "type": "Organization", "color": "#dbeafe"})
        edges.append({"from": "org", "to": "page", "rel": "publishes"})
    else:
        nodes.append({"id": "miss_org", "label": "Organization?", "type": "Missing", "style": "dashed", "color": "#fecaca"})
        edges.append({"from": "miss_org", "to": "page", "rel": "missing", "style": "missing"})

    if person_obj:
        pname = str(person_obj.get("name") or "Author")
        nodes.append({"id": "author", "label": pname, "type": "Person", "color": "#fce7f3"})
        edges.append({"from": "author", "to": "page", "rel": "creates"})
        if org_obj:
            edges.append({"from": "author", "to": "org", "rel": "works_for"})
    elif author_visible:
        nodes.append({"id": "txt_auth", "label": "Author (Text)", "type": "Text Signal", "style": "dashed", "color": "#ffedd5"})
        edges.append({"from": "txt_auth", "to": "page", "rel": "detected"})
    else:
        nodes.append({"id": "miss_auth", "label": "Author?", "type": "Missing", "style": "dashed", "color": "#fecaca"})
        edges.append({"from": "miss_auth", "to": "page", "rel": "missing", "style": "missing"})

    if page_type == "Service Page":
        nodes.append({"id": "service", "label": "Service Offer", "type": "Service", "color": "#dcfce7"})
        edges.append({"from": "page", "to": "service", "rel": "offers"})
    
    # Product-centric graph for webshop PDPs
    if page_type == "Product Page":
        pname = (product_signals.get("product_name") or "Product").strip() or "Product"
        brand = (product_signals.get("brand") or "").strip()
        collection = (product_signals.get("collection") or "").strip()
        price = (product_signals.get("price") or "").strip()
        currency = (product_signals.get("currency") or "").strip()
        availability = (product_signals.get("availability") or "").strip()

        # Core node
        nodes.append({"id": "product", "label": pname[:60], "type": "Product", "group": "product", "color": "#a7f3d0", "size": 36})
        edges.append({"from": "page", "to": "product", "rel": "about"})

        if brand:
            nodes.append({"id": "brand", "label": brand[:50], "type": "Brand", "group": "brand", "color": "#bfdbfe", "size": 24})
            edges.append({"from": "product", "to": "brand", "rel": "brand"})

        if collection:
            nodes.append({"id": "collection", "label": collection[:50], "type": "Collection", "group": "collection", "color": "#e9d5ff", "size": 22})
            edges.append({"from": "product", "to": "collection", "rel": "part_of"})

        has_offer_bits = bool(price or availability or product_signals.get("offers_count"))
        if has_offer_bits:
            nodes.append({"id": "offer", "label": "Offer", "type": "Offer", "group": "offer", "color": "#fde68a", "size": 22})
            edges.append({"from": "product", "to": "offer", "rel": "offers"})

            if price:
                price_label = price
                if currency and currency.upper() not in price_label.upper():
                    price_label = f"{price} {currency.upper()}"
                nodes.append({"id": "price", "label": price_label[:30], "type": "Price", "group": "price", "color": "#fff7ed", "size": 18})
                edges.append({"from": "offer", "to": "price", "rel": "price"})

            if availability:
                nodes.append({"id": "availability", "label": availability[:30], "type": "Availability", "group": "availability", "color": "#f1f5f9", "size": 18})
                edges.append({"from": "offer", "to": "availability", "rel": "availability"})

        variants = product_signals.get("variants") or []
        for i, v in enumerate(variants[:6]):
            vid = f"variant_{i+1}"
            nodes.append({"id": vid, "label": str(v)[:40], "type": "Variant", "group": "variant", "color": "#dcfce7", "size": 16})
            edges.append({"from": "product", "to": vid, "rel": "has_variant", "style": "weak"})

    cited = []
    for u in ext_links:
        lu = u.lower()
        if any(k in lu for k in ["mst.dk", "miljo", "miljø", "ds.dk", "iso", "ecolabel", "trustpilot", "sds", "sikkerhedsdatablad"]):
            cited.append(u)
    cited = list(dict.fromkeys(cited))[:4]
    for i, u in enumerate(cited):
        nid = f"c{i}"
        label = re.sub(r"^https?://", "", u).split("/")[0]
        nodes.append({"id": nid, "label": label, "type": "Cited", "color": "#f1f5f9"})
        edges.append({"from": "page", "to": nid, "rel": "cites"})

    # Optional dependency (better entity extraction)
    try:
        import spacy  # type: ignore
    except Exception:
        spacy = None
    # --- Topic entities (brands/products/etc.) extracted from content ---
    # The old map looked "empty" because we only mapped schema + a few cited links.
    # This adds the missing layer: entities mentioned in the content itself.
    topics = extract_topic_entities(title=title, headings=headings, text=text, max_entities=18)

    def _safe_node_id(prefix: str, label: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_]+", "_", label.strip())
        base = base.strip("_")
        if not base:
            base = "x"
        return f"{prefix}{base[:40]}"

    existing_ids = {n.get("id") for n in nodes if n.get("id")}
    for ent, c in topics:
        nid = _safe_node_id("t_", ent)
        # Ensure uniqueness
        k = 1
        while nid in existing_ids:
            k += 1
            nid = f"{_safe_node_id('t_', ent)}_{k}"
        existing_ids.add(nid)

        # Size scaling (bounded) – makes the map feel like the reference screenshot
        size = int(clamp(12 + (c * 2), 14, 36))

        nodes.append(
            {
                "id": nid,
                "label": ent,
                "type": "Topic",
                "group": "topic",
                "size": size,
                "color": "#22c55e",
            }
        )

        # Connect topics to the main entity if present, otherwise to the page
        if any(n.get("id") == "product" for n in nodes):
            hub = "product"
        else:
            hub = "org" if any(n.get("id") == "org" for n in nodes) else "page"
        edges.append({"from": hub, "to": nid, "rel": "mentions", "style": "weak"})

    entity_payload = {"nodes": nodes, "edges": edges}

    # Detected signals payload (for UI/debug)
    detected = {
        "word_count": word_count,
        "meta_description": bool(meta_desc),
        "canonical": bool(has_canonical),
        "schema_count": len(clean_schema_types),
        "has_org_schema": bool(("Organization" in clean_schema_types) or ("LocalBusiness" in clean_schema_types)),
        "has_service_schema": bool("Service" in clean_schema_types),
        "has_review_schema": bool(has_review_schema),
        "reviews_mentioned": bool(reviews_mentioned),
        "pricing_signal": bool(has_pricing),
        "process_signal": bool(has_process),
        "service_area_signal": bool(has_service_area),
        "faq_signal": bool(has_faq_like),
        "before_after_signal": bool(has_before_after),
        "contact_cta_signal": bool(has_contact_cta),
        "guarantee_signal": bool(has_guarantee),
        "guarantee_years_signal": bool(has_guarantee_years),
        "guarantee_terms_signal": bool(has_terms),
        "external_citations": external_citations,
        "trusted_out_links": len(trusted_out_links),
        "NAP": {
            "phone": nap.get("phone"),
            "email": nap.get("email"),
            "address": nap.get("address"),
            "cvr": nap.get("cvr"),
        },
        "social_links_count": len(socials),
        "usp_count": usp_count,
        "usp_years_experience": usps.get("years_experience"),
        "usp_specialist": bool(usps.get("has_specialist")),
        "usp_authorization": bool(usps.get("has_authorization")),
        "usp_awards": bool(usps.get("has_awards")),
        "usp_star_claim": bool(usps.get("has_star_claim")),
        "review_trustpilot_link": bool(has_trustpilot_link),
        "review_google_link": bool(has_google_reviews_link),
    }

    # --- 6) Single source of truth for actions ---
    # We derive BOTH the score and the "prioritized actions" from `requirements`.
    # `findings` below are only the "smart"/nuanced observations. Missing requirements
    # are injected once (and only once) as prioritized actions.

    # Build todo_summary directly from requirements (no parallel rules).
    todo_summary: Dict[str, Any] = {}
    for _pillar, _reqs in (requirements or {}).items():
        items = [
            {
                "label": r.get("label"),
                "ok": bool(r.get("ok")),
                "detail": r.get("detail"),
                "approx_gain": float(r.get("impact_points") or 0.0),
            }
            for r in (_reqs or [])
        ]
        missing = [it for it in items if not it.get("ok")]
        done = [it for it in items if it.get("ok")]
        todo_summary[_pillar] = {
            "items": items,
            "missing": missing,
            "done": done,
            "missing_count": len(missing),
            "done_count": len(done),
        }

    def _severity_from_points(p: float, pillar_name: str) -> str:
        p = float(p or 0.0)
        if pillar_name == "Indexability":
            if p >= 3.0:
                return "Critical"
            if p >= 1.5:
                return "High"
            return "Medium" if p >= 1.0 else "Low"
        if p >= 3.0:
            return "High"
        if p >= 1.5:
            return "Medium"
        return "Low"

    def _impact_from_points(p: float) -> int:
        p = float(p or 0.0)
        if p >= 3.0:
            return 5
        if p >= 2.0:
            return 4
        if p >= 1.0:
            return 3
        if p >= 0.8:
            return 2
        return 1

    def _already_covered(pillar_name: str, label: str) -> bool:
        ll = (label or "").strip().lower()
        for f in findings:
            if f.pillar != pillar_name:
                continue
            t = (f.title or "").strip().lower()
            if not t:
                continue
            if ll == t or ll in t or t in ll:
                return True
        return False

    # Inject missing requirements once as the canonical prioritized action list.
    for _pillar, _reqs in (requirements or {}).items():
        for r in (_reqs or []):
            if r.get("ok"):
                continue
            label = (r.get("label") or "").strip()
            detail = (r.get("detail") or "").strip()
            pts = float(r.get("impact_points") or 0.0)
            if not label:
                continue
            if _already_covered(_pillar, label):
                continue

            findings.append(
                Finding(
                    pillar=_pillar,
                    severity=_severity_from_points(pts, _pillar),
                    title=label,
                    why="Dette signal mangler og trækker scoren ned.",
                    how=detail or "Tilføj/ret dette signal på siden.",
                    impact=_impact_from_points(pts),
                    effort_minutes=20 if _pillar != "Indexability" else 10,
                    evidence=f"Mangler · ≈ +{pts:.1f}" if pts > 0 else "Mangler",
                )
            )

    # Final sort (one list, no duplicates)
    sev_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    findings.sort(key=lambda f: (sev_rank.get(f.severity, 9), -f.impact, f.effort_minutes))

    return overall, entity_score, cred_score, tech_score, findings, entity_payload, detected, todo_summary


# ------------------------------------------------------------
# UI
# ------------------------------------------------------------
st.markdown(
    "<h1 style='text-align: center; margin-bottom: 10px;'>GEO <span style='color:#3b82f6'>Checker</span></h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='text-align: center; color: #64748b; margin-bottom: 30px;'>GEO & AI-Readiness Audit</p>",
    unsafe_allow_html=True,
)

col_spacer1, col_input, col_spacer2 = st.columns([1, 2, 1])
with col_input:
    with st.container(border=True):
        mode = st.radio("Vælg input:", ["URL", "Indsæt indhold"], horizontal=True, label_visibility="collapsed")

        url = ""
        pasted = ""
        if mode == "URL":
            url = st.text_input("Indtast URL", placeholder="https://eksempel.dk/side", label_visibility="collapsed")
        else:
            pasted = st.text_area("Indhold", height=150, placeholder="<html> eller tekst...", label_visibility="collapsed")

        analyze = st.button("Kør Analyse ✨", type="primary", use_container_width=True)

if analyze:
    if mode == "URL" and not url.strip():
        st.error("Indtast URL")
        st.stop()
    if mode != "URL" and not pasted.strip():
        st.error("Indsæt indhold")
        st.stop()

    with st.spinner("Analyserer signaler..."):
        try:
            if mode == "URL":
                final_url, html, status, _headers = fetch_url_uncached(url)
            else:
                final_url, html, status, _headers = build_from_paste(pasted.strip())

            if not html:
                st.error("Kunne ikke hente indhold. Tjek URL eller prøv igen.")
                st.stop()

            text, title = extract_main_text_and_title(html)
            headings = extract_headings(html)
            internal_links, ext_links = extract_links(html, base_url=final_url if mode == "URL" else "")
            meta = extract_meta(html)
            indexability = compute_indexability(final_url if mode == "URL" else "", status, meta, _headers)
            nap = find_nap_signals(html)

            jsonld = extract_jsonld(html)
            schema_types, schema_objs = flatten_schema_types(jsonld)
            page_type = guess_page_type(title, headings, text, url=final_url if mode == "URL" else "", schema_types=schema_types)

            overall, s_ent, s_cred, s_tech, findings, entity_payload, detected, todo_summary = score_and_findings(
                page_type=page_type,
                title=title,
                text=text,
                headings=headings,
                schema_types=schema_types,
                schema_objs=schema_objs,
                internal_links=internal_links,
                ext_links=ext_links,
                meta=meta,
                nap=nap,
                indexability=indexability,
                final_url=final_url if mode == "URL" else "",
                raw_html=html,
            )
        except Exception as e:
            st.error(f"Fejl under analyse: {e}")
            st.stop()

    r_space1, r_content, r_space2 = st.columns([1, 3, 1])
    with r_content:
        st.markdown("---")

        c_head2, c_head3, c_head1 = st.columns([1.1, 1, 1.3])

        with c_head1:
            st.markdown('<div class="css-card" style="text-align:center;">', unsafe_allow_html=True)
            st.caption("AI READINESS")
            render_donut_score(overall)
            st.markdown('</div>', unsafe_allow_html=True)

        with c_head2:
            st.markdown('<div class="css-card">', unsafe_allow_html=True)
            st.caption("PAGE TYPE")
            st.markdown(f"### {page_type}")
            if page_type == "Service Page":
                st.caption("• Focus: Service Provider Entity, Schema Markup, Location, Purchase Signals")
            elif page_type == "Product Page":
                st.caption("• Focus: Product Entity, Offer, Price/Availability, Reviews")
            elif page_type == "Content / Article":
                st.caption("• Focus: Author Authority, Expertise, Citations")
            else:
                st.caption("• Focus: Entity signals, basic trust, technical hygiene")
            st.markdown('</div>', unsafe_allow_html=True)

        with c_head3:
            st.markdown('<div class="css-card">', unsafe_allow_html=True)
            st.caption("SCHEMA MARKUP")

            clean_found = [norm_schema_type(t) for t in schema_types]
            found_set = set(clean_found)

            # Requirements depend on page type, but Organization vs LocalBusiness is an either/or
            has_business_entity = ("Organization" in found_set) or ("LocalBusiness" in found_set)
            has_service = ("Service" in found_set)
            has_person = ("Person" in found_set)

            # Found list (what we actually detected)
            html_found = "<b>Found</b><br>"
            if not clean_found:
                html_found += "<span style='color:#cbd5e1'>None</span>"
            else:
                for f in clean_found[:8]:
                    html_found += (
                        f"<div class='status-item'><span class='status-icon found'>✓</span> {f}</div>"
                    )

            # Missing list (true requirements)
            html_missing = "<b>Missing (Critical)</b><br>"
            missing_items: List[str] = []

            if not has_business_entity:
                missing_items.append("Business Entity (Organization or LocalBusiness)")

            if page_type == "Service Page":
                if not has_service:
                    missing_items.append("Service")
            elif page_type == "Product Page":
                # Product pages should have Product + Offer (and ideally reviews when present)
                if "Product" not in found_set:
                    missing_items.append("Product")
                if "Offer" not in found_set:
                    missing_items.append("Offer")
            else:
                # For non-service pages we only require Person if the page is content/article-like
                if page_type == "Content / Article" and not has_person:
                    missing_items.append("Person")

            if not missing_items:
                html_missing += "<span style='color:#cbd5e1'>None</span>"
            else:
                for m in missing_items:
                    html_missing += (
                        f"<div class='status-item'><span class='status-icon missing'>⚠</span> {m}</div>"
                    )

            st.markdown(
                f"""
                <div style=\"display: flex; gap: 20px;\">
                    <div style=\"flex: 1;\">{html_found}</div>
                    <div style=\"flex: 1;\">{html_missing}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.markdown("</div>", unsafe_allow_html=True)


        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown('<div class="css-card" style="text-align:center;">', unsafe_allow_html=True)
            st.metric("Entity", f"{s_ent:.1f}/10")
            st.caption("Authority & Trust")
            st.markdown("</div>", unsafe_allow_html=True)
        with col2:
            st.markdown('<div class="css-card" style="text-align:center;">', unsafe_allow_html=True)
            st.metric("Credibility", f"{s_cred:.1f}/10")
            st.caption("Content & Sources")
            st.markdown("</div>", unsafe_allow_html=True)
        with col3:
            st.markdown('<div class="css-card" style="text-align:center;">', unsafe_allow_html=True)
            st.metric("Technical", f"{s_tech:.1f}/10")
            st.caption("Code & Schema")
            st.markdown("</div>", unsafe_allow_html=True)

        wins = quick_wins(findings)

        # --- Quick Wins section (before Detected Signals expander) ---
        if wins:
            st.markdown("## ⚡ Quick Wins")
            st.caption("Høj effekt, lav indsats – prioriter disse først")

            for i, w in enumerate(wins, start=1):
                with st.container(border=True):
                    st.markdown(
                        f"**{i}. {w.title}**  \n"
                        f"<span class='badge badge-{w.severity}'>{w.severity}</span> "
                        f"Impact: <b>{w.impact}/5</b> • Tid: <b>{w.effort_minutes} min</b>",
                        unsafe_allow_html=True,
                    )
                    st.write(w.why)
                    st.markdown("**Forslag:**")
                    st.write(w.how)
                    if w.evidence:
                        st.caption(f"Evidence: {w.evidence}")

        # Detected Signals (so you can verify the analysis is not generic)
        with st.expander("🔎 Detected Signals"):
            st.json(detected)
            st.caption(
                f"To-do counts — Entity: {(todo_summary.get('Entity Authority', {}).get('missing_count') if todo_summary else 0)} · "
                f"Credibility: {(todo_summary.get('Content Credibility', {}).get('missing_count') if todo_summary else 0)} · "
                f"Technical: {(todo_summary.get('Technical Signals', {}).get('missing_count') if todo_summary else 0)} · "
                f"Indexability: {(todo_summary.get('Indexability', {}).get('missing_count') if todo_summary else 0)}"
            )

        st.subheader("📋 Detaljeret Rapport")
        tab1, tab2, tab3, tab4 = st.tabs([
            "🏛️ Entity Authority",
            "📚 Content Credibility",
            "⚙️ Technical Signals",
            "🧭 Indexability",
        ])

        def render_findings_list(target_pillar: str):
            fs = [f for f in findings if f.pillar == target_pillar]
            if not fs:
                st.success("✅ Ingen problemer fundet.")
                return

            sev_icon = {
                "Critical": "🟥",
                "High": "🟧",
                "Medium": "🟨",
                "Low": "🟩",
            }

            for f in fs:
                # Card header so severity is visible without opening details
                with st.container(border=True):
                    icon = sev_icon.get(f.severity, "⬜")
                    st.markdown(
                        f"{icon} <span class='badge badge-{f.severity}'>{f.severity}</span> "
                        f"<b>{f.title}</b> &nbsp;·&nbsp; "
                        f"Impact: <b>{f.impact}/5</b> &nbsp;·&nbsp; Tid: <b>{f.effort_minutes} min</b>",
                        unsafe_allow_html=True,
                    )

                    with st.expander("Se detaljer"):
                        st.markdown("---")

                        c1, c2 = st.columns([1.2, 1])
                        with c1:
                            st.markdown("#### PROBLEM")
                            st.write(f.why)
                            st.markdown("#### LØSNING")
                            st.write(f.how)
                            if f.evidence:
                                st.caption(f"Evidence: {f.evidence}")

                        with c2:
                            if f.snippet:
                                st.markdown("#### 💻 COPY/PASTE KODE")
                                st.code(f.snippet, language="json")
                            else:
                                st.info("Ingen kode-snippet nødvendig.")

        with tab1:
            render_findings_list("Entity Authority")

        with tab2:
            render_findings_list("Content Credibility")

        with tab3:
            render_findings_list("Technical Signals")

        with tab4:

            label = indexability.get("label") or "Uncertain"
            blocked = bool(indexability.get("blocked"))
            reasons = indexability.get("blocked_reasons") or []

            if blocked:
                st.error(f"❌ {label}")
                if reasons:
                    st.markdown("**Årsager:**")
                    for r in reasons:
                        st.markdown(f"- {r}")
                st.markdown(
                    "Når en side ikke kan indekseres, ignorerer AI og søgemaskiner "
                    "ofte størstedelen af øvrige signaler."
                )
            else:
                st.success(f"✅ {label}")
                st.markdown("Ingen blokerende signaler (noindex, robots.txt eller HTTP-fejl) blev fundet.")

            st.markdown("---")
            st.markdown("**Tekniske signaler tjekket:**")
            st.markdown(
                "- HTTP-statuskode\n"
                "- meta robots\n"
                "- X-Robots-Tag headers\n"
                "- robots.txt (User-agent: *)"
            )

        st.subheader("🕸️ Entity Relationship Map")
        with st.container(border=True):
            render_entity_map(entity_payload)

        st.markdown("---")
        st.subheader("💻 Schema Templates")
        snippets = schema_snippet_suggestions(page_type)
        t1, t2, t3, t4 = st.tabs(["Organization", "LocalBusiness", "Person", "Service"])
        with t1:
            st.code(snippets["Organization"], language="json")
        with t2:
            st.code(snippets["LocalBusiness"], language="json")
        with t3:
            st.code(snippets["Person"], language="json")
        with t4:
            if "Service" in snippets:
                st.code(snippets["Service"], language="json")
            else:
                st.write("Service schema er primært relevant for service-sider.")

        with st.expander("🛠️ Debug Data"):
            st.json(
                {
                    "Final URL": final_url,
                    "Title": title,
                    "H1": headings.get("h1"),
                    "H2 count": len(headings.get("h2", [])),
                    "Word count": len(text.split()),
                    "Links Out": len(ext_links),
                    "Found Schema": schema_types,
                    "Status": status,
                    "Indexability": indexability,
                    "Meta": meta,
                    "NAP": nap,
                }
            )