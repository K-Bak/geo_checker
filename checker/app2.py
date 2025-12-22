import re
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

import streamlit as st
import requests
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
import graphviz


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
    .stApp { background-color: #f8f9fa; }

    .css-card {
        background-color: #ffffff;
        border-radius: 12px;
        padding: 24px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        border: 1px solid #e9ecef;
        margin-bottom: 20px;
        height: 100%;
    }

    h1, h2, h3 { color: #1e293b; font-family: 'Helvetica Neue', sans-serif; }
    h4 {
        color: #475569;
        font-size: 14px;
        text-transform: uppercase;
        margin-bottom: 10px;
        letter-spacing: 0.5px;
    }

    div[data-testid="stMetricValue"] { font-size: 28px; color: #0f172a; font-weight: 700; }
    div[data-testid="stMetricLabel"] { color: #64748b; font-size: 14px; }

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

    .streamlit-expanderHeader {
        background-color: #ffffff;
        border-radius: 8px;
        border: 1px solid #f1f5f9;
    }

    .pill {
        display:inline-block;
        padding: 3px 10px;
        border-radius: 999px;
        font-size: 12px;
        border: 1px solid #e2e8f0;
        background: #f8fafc;
        color: #334155;
        margin-right: 8px;
        margin-top: 6px;
    }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# --- SIKKERHEDSTJEK ---
# ---------------------------------------------------------
query_params = st.query_params
if query_params.get("access") != "GeneraxionKey":
    st.error("⛔ Adgang nægtet.")
    st.info("Denne app kan kun tilgås gennem Generaxions interne systemer.")
    st.stop()


# ------------------------------------------------------------
# Models
# ------------------------------------------------------------
@dataclass
class Finding:
    pillar: str  # Entity Authority | Content Credibility | Technical Signals | Indexability
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

HIGH_TRUST_HINTS = (
    "mst.dk", "miljo", "miljø", "ds.dk", "iso.org", "ecolabel",
    "svanemaerket", "svanemærket", "sikkerhedsdatablad", "sds",
    "ft.dk", "europa.eu", "sst.dk", "retsinformation.dk"
)

SOCIAL_HINTS = (
    "facebook.com", "instagram.com", "linkedin.com", "tiktok.com",
    "youtube.com", "x.com", "twitter.com", "trustpilot.com",
    "google.com/maps"
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

def normalize_phone(p: str) -> str:
    if not p:
        return ""
    return re.sub(r"[^\d+]", "", p)

def as_abs(url: str, base: str) -> str:
    try:
        return urljoin(base, url)
    except Exception:
        return url

def uniq(seq: List[str]) -> List[str]:
    return list(dict.fromkeys([s for s in seq if s]))


# ------------------------------------------------------------
# Fetching
# ------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=60 * 30)
def fetch_url_cached(url: str) -> Dict[str, Any]:
    return fetch_url_uncached(url)

def fetch_url_uncached(url: str) -> Dict[str, Any]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "da,en-US;q=0.8,en;q=0.7",
    }
    try:
        r = requests.get(url, headers=headers, timeout=25, allow_redirects=True)
        if not r.encoding:
            r.encoding = r.apparent_encoding

        chain = []
        try:
            for h in r.history:
                chain.append({"url": getattr(h, "url", ""), "status": getattr(h, "status_code", 0)})
        except Exception:
            pass

        return {
            "final_url": r.url,
            "html": r.text or "",
            "status": r.status_code,
            "headers": dict(r.headers),
            "redirect_chain": chain,
        }
    except Exception as e:
        return {"final_url": url, "html": "", "status": 0, "headers": {"Error": str(e)}, "redirect_chain": []}

def fetch_url_playwright(url: str) -> Dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError(
            "Playwright er ikke installeret. Kør: pip install playwright  &&  playwright install chromium"
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="da-DK",
            extra_http_headers={"Accept-Language": "da,en-US;q=0.8,en;q=0.7"},
        )
        page = context.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=35_000)
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass

            final_url = page.url
            html = page.content()
            status = resp.status if resp else 0
            headers = dict(resp.headers) if resp else {}
        except Exception:
            return {"final_url": url, "html": "", "status": 0, "headers": {}, "redirect_chain": []}
        finally:
            context.close()
            browser.close()

    return {"final_url": final_url, "html": html or "", "status": status, "headers": headers, "redirect_chain": []}

def build_from_paste(pasted_content: str) -> Dict[str, Any]:
    if "<" not in pasted_content and ">" not in pasted_content:
        html = (
            "<html><head><title></title></head>"
            "<body><main><p>"
            + BeautifulSoup(pasted_content, "html.parser").get_text(" ", strip=True)
            + "</p></main></body></html>"
        )
        return {"final_url": "(pasted)", "html": html, "status": 200, "headers": {}, "redirect_chain": []}
    return {"final_url": "(pasted)", "html": pasted_content, "status": 200, "headers": {}, "redirect_chain": []}


# ------------------------------------------------------------
# Parsing
# ------------------------------------------------------------
def extract_html_lang(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("html")
        if tag and tag.get("lang"):
            return (tag.get("lang") or "").strip().lower()
    except Exception:
        pass
    return ""

def extract_hreflang(html: str) -> List[str]:
    try:
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for link in soup.find_all("link", rel=lambda x: x and "alternate" in x):
            hreflang = (link.get("hreflang") or "").strip().lower()
            href = (link.get("href") or "").strip()
            if hreflang and href:
                out.append(f"{hreflang}: {href}")
        return out
    except Exception:
        return []

def extract_main_text_and_title(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(" ", strip=True)

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

    internal = [as_abs(u, base_url) if base_url else u for u in internal]
    return uniq(internal), uniq(external)

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

def find_nap_signals(html: str) -> Dict[str, Optional[str]]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    cvr = None
    m = re.search(r"\bCVR\s*(?:[-\s]*nr\.?\s*)?[:.]?\s*(\d(?:\s*\d){7})\b", text, re.I)
    if m:
        cvr = re.sub(r"\s+", "", m.group(1))

    email = None
    m = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    if m:
        email = m.group(0)

    phone = None
    m = re.search(r"(\+?\s*45\s*)?(\d[\d\s\-]{6,}\d)", text)
    if m:
        phone = normalize_phone(m.group(0))

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

def schema_find_service(objs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for o in objs:
        t = o.get("@type")
        ts = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
        ts = [str(x).lower() for x in ts]
        if "service" in ts:
            return o
    return None

def detect_social_links(ext_links: List[str]) -> List[str]:
    return sorted({u for u in ext_links if any(d in u.lower() for d in SOCIAL_HINTS)})

def classify_out_links(ext_links: List[str]) -> Dict[str, List[str]]:
    high = []
    social = []
    other = []
    for u in ext_links:
        lu = u.lower()
        if any(s in lu for s in SOCIAL_HINTS):
            social.append(u)
        elif any(h in lu for h in HIGH_TRUST_HINTS):
            high.append(u)
        else:
            other.append(u)
    return {"high_trust": uniq(high), "social": uniq(social), "other": uniq(other)}

def count_external_citations(ext_links: List[str]) -> int:
    c = classify_out_links(ext_links)
    cites = c["high_trust"] + c["other"]
    return len(set(cites))

def guess_page_type(title: str, headings: Dict[str, List[str]], text: str) -> str:
    hay = " ".join([title] + headings.get("h1", []) + headings.get("h2", [])).lower()
    service_terms = [
        "service", "ydelse", "vi tilbyder", "pris", "tilbud", "bestil",
        "kontakt", "fliserens", "tagrens", "facaderens", "alge",
        "imprægner", "rengøring", "behandling", "rens", "terrasse",
    ]
    blog_terms = ["blog", "nyhed", "artikel", "guide", "sådan", "tips", "viden", "råd"]

    if any(t in hay for t in service_terms):
        return "Service Page"
    if any(t in hay for t in blog_terms):
        return "Content / Article"
    if len(text) < 1500:
        return "Service Page"
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
    if count_external_citations(ext_links) > 1:
        return []
    matches: List[str] = []
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            matches.append(m.group(0))
    return list(set(matches))

def quick_wins(findings: List[Finding], max_items: int = 6) -> List[Finding]:
    wins = [f for f in findings if f.impact >= 4 and f.effort_minutes <= 30]
    return wins[:max_items]

def parse_robots_directives(meta_robots: str, x_robots: str) -> Dict[str, bool]:
    blob = ",".join([meta_robots or "", x_robots or ""]).lower()
    blob = re.sub(r"\s+", "", blob)
    return {
        "noindex": "noindex" in blob,
        "nofollow": "nofollow" in blob,
        "noarchive": "noarchive" in blob,
        "nosnippet": "nosnippet" in blob,
    }

def best_internal_candidates(internal_links: List[str], final_url: str) -> Dict[str, Optional[str]]:
    # Prioritér “trust pages”
    patterns = {
        "contact": [r"/kontakt", r"/contact"],
        "about": [r"/om", r"/om-os", r"/about", r"/about-us"],
        "privacy": [r"/privacy", r"/privatliv", r"/cookie", r"/cookies", r"/gdpr"],
    }

    out = {"contact": None, "about": None, "privacy": None}
    if not internal_links:
        return out

    for k, pats in patterns.items():
        for u in internal_links:
            lu = (u or "").lower()
            if any(re.search(p, lu) for p in pats):
                out[k] = as_abs(u, final_url)
                break
    return out

def schema_org_completeness(org: Dict[str, Any]) -> Dict[str, bool]:
    # Minimal "wow" checks: @id, name, url, logo, sameAs, telephone/email, address
    has_id = bool(str(org.get("@id") or "").strip())
    has_name = bool(str(org.get("name") or "").strip())
    has_url = bool(str(org.get("url") or "").strip())
    has_logo = bool(str(org.get("logo") or "").strip())
    sameAs = org.get("sameAs")
    has_sameAs = bool(sameAs) and (isinstance(sameAs, list) and any(str(x).strip() for x in sameAs))
    has_phone = bool(str(org.get("telephone") or "").strip())
    has_email = bool(str(org.get("email") or "").strip())
    addr = org.get("address")
    has_address = isinstance(addr, dict) and any(str(addr.get(k) or "").strip() for k in ["streetAddress", "addressLocality", "postalCode", "addressCountry"])
    return {
        "has_id": has_id,
        "has_name": has_name,
        "has_url": has_url,
        "has_logo": has_logo,
        "has_sameAs": has_sameAs,
        "has_phone_or_email": (has_phone or has_email),
        "has_address": has_address,
    }

def intent_coverage_service(text: str, headings: Dict[str, List[str]]) -> Dict[str, bool]:
    h = " ".join(headings.get("h2", []) + headings.get("h3", [])).lower()
    t = (text or "").lower()

    signals = {
        "pricing": bool(re.search(r"\b(pris|priser|fra\s+\d+|kr\.?|dkk)\b", t, re.I)),
        "process": bool(re.search(r"\b(sådan\s+foregår|proces|trin\s+\d|step\s+\d|fremgangsmåde)\b", t, re.I)),
        "time_expectation": bool(re.search(r"\b(timer|minutter|dage|leveringstid|responstid)\b", t, re.I)),
        "risk_tradeoffs": bool(re.search(r"\b(forbehold|risiko|begrænsning|kan\s+ikke|afhænger\s+af)\b", t, re.I)),
        "materials_tools": bool(re.search(r"\b(materialer|produkter|kemikal|udstyr|maskin|metode)\b", t, re.I)),
        "cases_before_after": bool(re.search(r"\b(før\s+og\s+efter|case|resultat|før/efter)\b", t, re.I)),
        "faq": bool(re.search(r"\b(faq|ofte\s+stillede|spørgsmål)\b", t, re.I)) or any("?" in x for x in headings.get("h2", [])),
        "service_area": bool(re.search(r"\b(vi\s+kører|dækker|område|hele\s+danmark|sjælland|jylland|fyn|københavn|aarhus)\b", t, re.I)),
        "contact_cta": bool(re.search(r"\b(kontakt\s+os|ring\s+nu|få\s+tilbud|book|bestil)\b", t, re.I)),
    }

    # “What is this service?” proxy: find definition-ish lines in headings
    signals["what_is_it"] = bool(re.search(r"\b(hvad\s+er|om\s+|vi\s+tilbyder)\b", h, re.I)) or len(headings.get("h2", [])) >= 2
    return signals


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

    colors = ["#3b82f6", "#e2e8f0"]

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
        style = e.get("style", "solid")
        color = "#dc2626" if style == "dashed" else "#94a3b8"
        graph.edge(e.get("from"), e.get("to"), label=e.get("rel", ""), style=style, color=color, fontcolor=color)

    st.graphviz_chart(graph, use_container_width=True)

def schema_snippet_suggestions(page_type: str) -> Dict[str, str]:
    org = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": "[https://eksempel.dk/#organization]",
        "name": "[Virksomhedsnavn]",
        "url": "[https://eksempel.dk]",
        "logo": "[https://eksempel.dk/logo.png]",
        "telephone": "[+45 xx xx xx xx]",
        "email": "[kontakt@eksempel.dk]",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "[Gade 1]",
            "postalCode": "[1234]",
            "addressLocality": "[By]",
            "addressCountry": "DK"
        },
        "sameAs": ["[Facebook URL]", "[LinkedIn URL]", "[Trustpilot URL]"],
    }
    local = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "@id": "[https://eksempel.dk/#organization]",
        "name": "[Virksomhedsnavn]",
        "url": "[https://eksempel.dk]",
        "areaServed": {"@type": "Country", "name": "Denmark"},
        "priceRange": "$$",
        "telephone": "[+45 xx xx xx xx]",
        "address": {"@type": "PostalAddress", "addressCountry": "DK"},
        "sameAs": ["[Facebook URL]", "[LinkedIn URL]", "[Trustpilot URL]"],
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

    out = {
        "Organization": json.dumps(org, ensure_ascii=False, indent=2),
        "LocalBusiness": json.dumps(local, ensure_ascii=False, indent=2),
        "Person": json.dumps(person, ensure_ascii=False, indent=2),
    }
    if page_type == "Service Page":
        out["Service"] = json.dumps(service, ensure_ascii=False, indent=2)
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
    fetch_meta: Dict[str, Any],
    # optional extras
    site_pages: Dict[str, Dict[str, Any]],
    render_parity: Dict[str, Any],
    html_lang: str,
    hreflang: List[str],
) -> Tuple[float, float, float, float, float, List[Finding], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    findings: List[Finding] = []

    # --- Base objects/signals ---
    org_obj = schema_find_org_like(schema_objs)
    person_obj = schema_find_person(schema_objs)
    service_obj = schema_find_service(schema_objs)

    out_class = classify_out_links(ext_links)
    socials = out_class["social"]
    high_trust_out = out_class["high_trust"]
    other_out = out_class["other"]

    internal_join = " ".join(internal_links).lower()
    has_about_link = any(k in internal_join for k in ["/om", "about", "om-os", "about-us"])
    has_contact_link = any(k in internal_join for k in ["/kontakt", "contact"])
    has_privacy_link = any(k in internal_join for k in ["/privacy", "privatliv", "cookie", "cookies", "gdpr"])

    external_citations = len(set(high_trust_out + other_out))
    unsourced_claims = detect_unsourced_claims(text, ext_links)

    author_visible = bool(re.search(r"\b(forfatter|skrevet af|author|by)\b", text, re.I))
    clean_schema_types = [norm_schema_type(t) for t in schema_types]
    has_review_schema = ("Review" in clean_schema_types) or ("AggregateRating" in clean_schema_types)
    reviews_mentioned = ("trustpilot" in text.lower()) or ("anmeldelse" in text.lower()) or ("stjerner" in text.lower())

    has_h1 = len(headings.get("h1", [])) > 0
    h2_count = len(headings.get("h2", []))

    # --- Page reality ---
    word_count = len(text.split())
    meta_desc = (meta.get("description") or "").strip()
    canonical = (meta.get("canonical") or "").strip()
    has_canonical = bool(canonical)

    # Service intent coverage
    svc_cov = intent_coverage_service(text, headings) if page_type == "Service Page" else {}

    # Guarantee
    has_guarantee = bool(re.search(r"\bgaranti\b", text, re.I))
    has_terms = bool(re.search(r"\b(gælder|forudsætter|vilkår|betingelser|undtaget|dokumentation)\b", text, re.I))

    # NAP presence
    nap_phone = bool(nap.get("phone"))
    nap_email = bool(nap.get("email"))
    nap_address = bool(nap.get("address"))
    nap_cvr = bool(nap.get("cvr"))

    # Indexability (meta + header)
    headers = fetch_meta.get("headers", {}) or {}
    x_robots = (headers.get("X-Robots-Tag") or headers.get("x-robots-tag") or "").strip()
    robots_meta = (meta.get("robots") or "").strip()
    robots_directives = parse_robots_directives(robots_meta, x_robots)

    status = int(fetch_meta.get("status") or 0)
    chain = fetch_meta.get("redirect_chain", []) or []
    chain_len = len(chain)

    # Lang sanity
    lang_ok = True
    if html_lang:
        # accept da / da-dk
        lang_ok = html_lang.startswith("da")

    # Site page cross-validation (about/contact/privacy)
    site_naps = {}
    site_schema_types = {}
    site_org_objs = {}
    for key, pdata in (site_pages or {}).items():
        site_naps[key] = pdata.get("nap") or {}
        site_schema_types[key] = pdata.get("schema_types") or []
        site_org_objs[key] = pdata.get("org_obj") or None

    # NAP consistency: if multiple pages have phone/email/address/cvr, check mismatches
    def collect_field(field: str) -> List[str]:
        vals = []
        main = nap.get(field)
        if main:
            vals.append(str(main))
        for _, sn in site_naps.items():
            v = sn.get(field)
            if v:
                vals.append(str(v))
        # normalize phones
        if field == "phone":
            vals = [normalize_phone(v) for v in vals]
        return uniq(vals)

    phone_vals = collect_field("phone")
    email_vals = collect_field("email")
    addr_vals = collect_field("address")
    cvr_vals = collect_field("cvr")

    nap_consistent = True
    nap_consistent = nap_consistent and (len(phone_vals) <= 1)
    nap_consistent = nap_consistent and (len(email_vals) <= 1)
    nap_consistent = nap_consistent and (len(addr_vals) <= 1)
    nap_consistent = nap_consistent and (len(cvr_vals) <= 1)

    # Schema completeness
    org_comp = schema_org_completeness(org_obj) if isinstance(org_obj, dict) else {}
    has_business_entity = ("Organization" in clean_schema_types) or ("LocalBusiness" in clean_schema_types)
    has_service_schema = ("Service" in clean_schema_types)
    has_person_schema = ("Person" in clean_schema_types)

    # Render parity (if present)
    parity_ratio = float(render_parity.get("ratio", 1.0)) if render_parity else 1.0
    parity_ok = parity_ratio >= 0.65  # under this: likely JS-only / hidden content

    # --- Scoring (0-10 per pillar) ---
    # Entity Authority
    entity_score = 0.0
    if person_obj or author_visible:
        entity_score += 2.0
    if org_obj:
        entity_score += 3.0
    if nap_phone or nap_email:
        entity_score += 1.0
    if nap_address:
        entity_score += 1.0
    if nap_cvr:
        entity_score += 1.0
    if len(socials) >= 2:
        entity_score += 1.3
    elif len(socials) == 1:
        entity_score += 0.7
    if has_about_link:
        entity_score += 0.7
    if has_contact_link:
        entity_score += 0.7
    if nap_consistent:
        entity_score += 0.6
    entity_score = clamp(entity_score, 0, 10)

    # Content Credibility
    cred_score = 0.0
    if external_citations >= 3:
        cred_score += 2.5
    elif external_citations >= 1:
        cred_score += 1.0
    if unsourced_claims and external_citations == 0:
        cred_score -= 1.0
    if len(high_trust_out) >= 1:
        cred_score += 1.5

    has_expert_quotes = bool(re.search(r"\b(siger|udtaler|ifølge|citat|kilde:)\b", text, re.I))
    if has_expert_quotes:
        cred_score += 1.2

    # Service usefulness rewards (more granular)
    if page_type == "Service Page":
        useful_hits = sum(1 for k, v in svc_cov.items() if v)
        cred_score += clamp(useful_hits * 0.35, 0, 3.0)

    if has_guarantee and has_terms:
        cred_score += 0.5

    if word_count < 450:
        cred_score -= 1.0
    elif word_count < 700:
        cred_score -= 0.3

    cred_score = clamp(cred_score, 0, 10)

    # Technical Signals (schema, meta, structure)
    tech_score = 0.0
    if clean_schema_types:
        tech_score += 2.0
    if page_type == "Service Page" and "Service" in clean_schema_types:
        tech_score += 2.0
    if has_business_entity:
        tech_score += 2.0
    if reviews_mentioned and has_review_schema:
        tech_score += 1.2
    if has_h1:
        tech_score += 1.0
    if meta_desc:
        tech_score += 0.6
    if has_canonical:
        tech_score += 0.6
    if has_privacy_link:
        tech_score += 0.6

    # Completeness bonus
    if org_comp and org_comp.get("has_id"):
        tech_score += 0.8
    if org_comp and org_comp.get("has_sameAs"):
        tech_score += 0.4

    tech_score = clamp(tech_score, 0, 10)

    # Indexability / AI Accessibility
    idx_score = 10.0
    if status >= 400 or status == 0:
        idx_score -= 4.0
    if robots_directives.get("noindex"):
        idx_score -= 5.0
    if chain_len >= 3:
        idx_score -= 1.0
    if not parity_ok:
        idx_score -= 2.0
    if html_lang and not lang_ok:
        idx_score -= 0.8
    idx_score = clamp(idx_score, 0, 10)

    overall = round(0.28 * entity_score + 0.30 * cred_score + 0.24 * tech_score + 0.18 * idx_score, 1)

    # --------------------------------------------------------
    # FINDINGS (WOW: more “real” issues)
    # --------------------------------------------------------
    # INDEXABILITY
    if status == 0:
        findings.append(Finding(
            "Indexability", "Critical",
            "Kunne ikke hente siden (timeout / blokering)",
            "Hvis vi ikke kan hente siden stabilt, kan crawlere/AI også have problemer.",
            "Prøv Playwright. Hvis det hjælper: fjern bot-blocking / whitelist, og sørg for server svarer stabilt.",
            5, 20,
            evidence=str(fetch_meta.get("headers", {}))
        ))

    if status >= 400 and status != 0:
        findings.append(Finding(
            "Indexability", "Critical",
            f"HTTP status er {status}",
            "Siden er reelt ikke tilgængelig eller returnerer fejl. Det dræber både SEO og AI-udtræk.",
            "Ret statuskoden (200) og tjek redirects/canonical.",
            5, 30,
            evidence=f"Status={status}, final_url={fetch_meta.get('final_url')}"
        ))

    if chain_len >= 3:
        findings.append(Finding(
            "Indexability", "Medium",
            "Lang redirect-kæde",
            "Flere redirects kan give ustabil crawl og dårligere signal-konsistens.",
            "Forkort redirect chain (ideelt 0–1).",
            3, 20,
            evidence=f"Redirect chain: {chain}"
        ))

    if robots_directives.get("noindex"):
        findings.append(Finding(
            "Indexability", "Critical",
            "Siden er markeret som NOINDEX",
            "Hvis siden ikke må indekseres, vil den typisk ikke blive brugt/valgt af søgemaskiner og kan også blive nedprioriteret i AI flows.",
            "Fjern noindex i meta robots eller X-Robots-Tag, hvis siden skal performe.",
            5, 10,
            evidence=f"meta robots='{robots_meta}' | X-Robots-Tag='{x_robots}'"
        ))

    if not parity_ok:
        findings.append(Finding(
            "Indexability", "High",
            "Meget indhold ser ud til at kræve JavaScript",
            "Hvis en stor del af teksten først kommer efter JS, mister du signaler i “simple crawls” og nogle AI pipelines.",
            "Server-render vigtigt indhold (eller sørg for prerender). Alternativt: sikr at main content findes i rå HTML.",
            4, 45,
            evidence=f"Render parity ratio ≈ {parity_ratio:.2f} (tekst uden JS vs med JS)"
        ))

    if html_lang and not lang_ok:
        findings.append(Finding(
            "Indexability", "Medium",
            f"HTML lang='{html_lang}' matcher ikke dansk fokus",
            "Forkert lang-attribut kan give dårligere matching i søgning/AI.",
            "Sæt korrekt lang (da / da-DK) i <html lang> og brug hreflang hvis flere sprog.",
            3, 10,
            evidence=f"lang={html_lang}, hreflang_count={len(hreflang)}"
        ))

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
                "@id": "https://eksempel.dk/#organization",
                "name": "Virksomhedsnavn",
                "telephone": "+45 xx xx xx xx",
                "email": "kontakt@eksempel.dk",
                "address": {"@type": "PostalAddress", "addressCountry": "DK"},
                "sameAs": ["https://dk.trustpilot.com/review/...", "https://www.facebook.com/..."]
            }, ensure_ascii=False, indent=2)
        ))

    if not nap_consistent and any([phone_vals, email_vals, addr_vals, cvr_vals]):
        findings.append(Finding(
            "Entity Authority", "High",
            "NAP er inkonsistent på tværs af siden og trust-sider",
            "Hvis telefon/email/adresse/CVR varierer, bliver entity-fortolkningen svagere og mindre troværdig.",
            "Ensret NAP på alle sider + i schema. Brug 1 sandhed (footer + schema) og genbrug den.",
            4, 30,
            evidence=f"phone={phone_vals}, email={email_vals}, address={addr_vals}, cvr={cvr_vals}"
        ))

    if org_obj and org_comp:
        missing_bits = [k for k, ok in org_comp.items() if not ok]
        if missing_bits:
            findings.append(Finding(
                "Technical Signals", "Medium",
                "Organization schema findes – men er ikke komplet",
                "Schema der er ‘halvt udfyldt’ giver lavere udbytte end et komplet entity-graph.",
                "Tilføj @id, url, logo, sameAs, telefon/email og adresse-felter. Brug @id som fælles reference.",
                3, 25,
                evidence=f"Mangler: {', '.join(missing_bits)}"
            ))

    if not (person_obj or author_visible) and page_type == "Content / Article":
        findings.append(Finding(
            "Entity Authority", "High",
            "Ingen forfatter/Person-attribution fundet",
            "Indhold fremstår anonymt. På artikler/guides er afsenderkritisk (E-E-A-T).",
            "Tilføj forfatterboks + Person schema (navn, rolle, credentials, sameAs).",
            4, 25,
            evidence="Ingen Person schema og ingen 'skrevet af/author' signal fundet.",
        ))

    if len(socials) == 0:
        findings.append(Finding(
            "Entity Authority", "High",
            "Ingen sameAs / sociale profiler linket",
            "AI bruger sociale profiler til at krydsvalidere at virksomheden er ægte og aktiv.",
            "Tilføj sameAs i Organization schema (Facebook/LinkedIn/Trustpilot hvis relevant).",
            4, 15,
            evidence="Ingen social links fundet blandt eksterne links.",
            snippet='"sameAs": ["https://www.facebook.com/dinside", "https://www.linkedin.com/company/dinside", "https://dk.trustpilot.com/review/..."]'
        ))

    if not nap.get("cvr"):
        findings.append(Finding(
            "Entity Authority", "Medium",
            "CVR-nummer ikke fundet",
            "CVR er et stærkt DK-trustsignal og gør virksomheden let at verificere.",
            "Vis CVR i footer/kontaktsektion (og gerne i Organization schema).",
            3, 10,
            evidence="Ingen 'CVR' + 8 cifre fundet i HTML-tekst."
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
            "Tilføj vilkår (hvad gælder det, hvad er undtaget, hvordan dokumenteres det) + link til garanti-side.",
            3, 20,
            evidence="Garanti fundet, men ingen vilkårs-ord (gælder/forudsætter/vilkår/betingelser/undtaget) fundet."
        ))

    if page_type == "Service Page":
        missing_blocks = [k for k, v in svc_cov.items() if not v]
        # Kun push de vigtigste blokke som findings (ellers bliver rapporten for lang)
        important = [k for k in missing_blocks if k in ["pricing", "process", "risk_tradeoffs", "faq", "service_area", "contact_cta"]]
        if important:
            findings.append(Finding(
                "Content Credibility", "High",
                "Servicesiden mangler centrale ‘købbarhed’-blokke",
                "AI og kunder stoler mere på en service, når den forklarer prislogik, proces, forbehold og næste skridt.",
                "Tilføj de manglende blokke (kort og konkret): prisfaktorer, 3–6 trin proces, forbehold, FAQ, serviceområde og CTA.",
                4, 40,
                evidence=f"Mangler: {', '.join(important)}"
            ))

    if word_count < 450:
        findings.append(Finding(
            "Content Credibility", "High",
            "Tyndt indhold (lav tekstmængde)",
            "Korte servicesider giver få entiteter og lav topic coverage.",
            "Udbyg med FAQ, metode, materialer, garanti-betingelser, cases, serviceområde og forventninger.",
            4, 30,
            evidence=f"Ordantal ≈ {word_count}"
        ))

    # TECHNICAL
    if not meta_desc:
        findings.append(Finding(
            "Technical Signals", "Low",
            "Meta description mangler",
            "Lavere kvalitet af snippets og svagere sidebeskrivelse for søgning/AI.",
            "Tilføj unik meta description (140–160 tegn) med ydelse + område + proof.",
            2, 10,
            evidence="Ingen <meta name='description'> fundet."
        ))

    if not has_canonical:
        findings.append(Finding(
            "Technical Signals", "Low",
            "Canonical link mangler",
            "Canonical hjælper AI/søgemaskiner med at forstå 'den rigtige' version af siden.",
            "Tilføj canonical-tag (især vigtigt ved filtre/parametre).",
            2, 10,
            evidence="Ingen <link rel='canonical'> fundet."
        ))
    else:
        # Canonical sanity: host mismatch
        final_host = get_hostname(fetch_meta.get("final_url") or "")
        canon_host = get_hostname(canonical)
        if final_host and canon_host and final_host != canon_host:
            findings.append(Finding(
                "Technical Signals", "High",
                "Canonical peger på andet domæne/host",
                "Det kan splitte signaler og skabe uklarhed om hvilken URL der er ‘master’.",
                "Sørg for canonical peger på korrekt host (samme primære domæne).",
                4, 15,
                evidence=f"final_host={final_host}, canonical_host={canon_host}, canonical={canonical}"
            ))

    if page_type == "Service Page" and "Service" not in clean_schema_types:
        findings.append(Finding(
            "Technical Signals", "Critical",
            "Mangler Service schema på serviceside",
            "AI forstår ikke fuldt ud at dette er en ydelse, og hvordan den relaterer til udbyderen.",
            "Tilføj Service JSON-LD pr. ydelse og link provider til Organization/@id.",
            5, 30,
            evidence="Page type = Service Page, men Service schema er ikke fundet.",
            snippet=json.dumps({
                "@context": "https://schema.org",
                "@type": "Service",
                "serviceType": "Fliserens",
                "provider": {"@id": "https://eksempel.dk/#organization"},
                "areaServed": {"@type": "Country", "name": "Denmark"}
            }, ensure_ascii=False, indent=2)
        ))

    if service_obj and org_obj:
        # Check provider @id relation (basic)
        provider = service_obj.get("provider") if isinstance(service_obj, dict) else None
        org_id = str(org_obj.get("@id") or "").strip() if isinstance(org_obj, dict) else ""
        prov_id = ""
        if isinstance(provider, dict):
            prov_id = str(provider.get("@id") or "").strip()
        if org_id and provider and prov_id and (org_id != prov_id):
            findings.append(Finding(
                "Technical Signals", "Medium",
                "Service provider @id matcher ikke Organization @id",
                "Når graph’et ikke hænger sammen, mister schema noget af sin værdi for AI.",
                "Sørg for at Service.provider.@id refererer til Organization/@id.",
                3, 20,
                evidence=f"org.@id={org_id} vs service.provider.@id={prov_id}"
            ))

    if reviews_mentioned and not has_review_schema:
        findings.append(Finding(
            "Technical Signals", "High",
            "Reviews nævnes men ingen schema",
            "Du viser anmeldelser til mennesker, men gør dem svære for AI at aflæse struktureret.",
            "Tilføj AggregateRating eller Review schema (og match med reelle tal).",
            4, 45,
            evidence="Trustpilot/anmeldelse/stjerner nævnt i tekst, men Review/AggregateRating ikke fundet i schema.",
        ))

    if page_type == "Service Page":
        has_faq_like = svc_cov.get("faq", False)
        if has_faq_like and "FAQPage" not in clean_schema_types:
            findings.append(Finding(
                "Technical Signals", "Medium",
                "FAQ-indhold uden FAQPage schema",
                "Hvis du allerede har FAQ-lignende indhold, kan schema give bedre udtræk til AI.",
                "Markér Q&A som FAQPage schema.",
                3, 25,
                evidence="FAQ-signaler fundet men ingen FAQPage schema."
            ))

    if not has_privacy_link:
        findings.append(Finding(
            "Technical Signals", "Low",
            "Ingen tydelig privacy/cookie-side fundet i interne links",
            "Det er et trust-signal at have synlig GDPR/cookie/privatliv (især i EU).",
            "Tilføj link til cookie-/privatlivspolitik i footer.",
            2, 15,
            evidence="Ingen interne links der matcher privacy/cookie/gdpr."
        ))

    # Sortering
    sev_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    findings.sort(key=lambda f: (sev_rank.get(f.severity, 9), -f.impact, f.effort_minutes))

    # --- Entity map (extended) ---
    nodes: List[Dict[str, Any]] = [{"id": "page", "label": "WebPage", "type": "Page", "color": "#e2e8f0"}]
    edges: List[Dict[str, Any]] = []

    if org_obj:
        name = str(org_obj.get("name") or "Organization")
        nodes.append({"id": "org", "label": name, "type": "Organization", "color": "#dbeafe"})
        edges.append({"from": "org", "to": "page", "rel": "publishes"})
    else:
        nodes.append({"id": "miss_org", "label": "Organization?", "type": "Missing", "style": "dashed", "color": "#fecaca"})
        edges.append({"from": "miss_org", "to": "page", "rel": "missing", "style": "dashed"})

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
        edges.append({"from": "miss_auth", "to": "page", "rel": "missing", "style": "dashed"})

    if page_type == "Service Page":
        nodes.append({"id": "service", "label": "Service Offer", "type": "Service", "color": "#dcfce7"})
        edges.append({"from": "page", "to": "service", "rel": "offers"})

    cited = uniq(high_trust_out)[:4]
    for i, u in enumerate(cited):
        nid = f"c{i}"
        label = re.sub(r"^https?://", "", u).split("/")[0]
        nodes.append({"id": nid, "label": label, "type": "High Trust Source", "color": "#f1f5f9"})
        edges.append({"from": "page", "to": nid, "rel": "cites"})

    entity_payload = {"nodes": nodes, "edges": edges}

    detected = {
        "status": status,
        "redirect_chain_len": chain_len,
        "redirect_chain": chain,
        "meta_robots": robots_meta,
        "x_robots_tag": x_robots,
        "robots_directives": robots_directives,
        "render_parity_ratio": parity_ratio,
        "html_lang": html_lang,
        "hreflang": hreflang[:10],
        "word_count": word_count,
        "meta_description": bool(meta_desc),
        "canonical": canonical,
        "schema_count": len(clean_schema_types),
        "found_schema_types": clean_schema_types[:30],
        "schema_has_business_entity": has_business_entity,
        "schema_has_service": has_service_schema,
        "schema_has_person": has_person_schema,
        "schema_has_review": has_review_schema,
        "reviews_mentioned": bool(reviews_mentioned),
        "citations": {
            "high_trust": len(high_trust_out),
            "social": len(socials),
            "other": len(other_out),
        },
        "NAP_main": nap,
        "NAP_values_across_site": {
            "phone": phone_vals,
            "email": email_vals,
            "address": addr_vals,
            "cvr": cvr_vals,
            "consistent": nap_consistent,
        },
        "service_intent_coverage": svc_cov,
        "site_pages_checked": list((site_pages or {}).keys()),
    }

    scores = {"overall": overall, "entity": entity_score, "cred": cred_score, "tech": tech_score, "indexability": idx_score}

    return overall, entity_score, cred_score, tech_score, idx_score, findings, entity_payload, detected, scores


def build_sales_summary(
    final_url: str,
    page_type: str,
    scores: Dict[str, float],
    findings: List[Finding],
    detected: Dict[str, Any],
) -> Dict[str, Any]:
    # Top problems = top 3 severe findings
    sev_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    top = sorted(findings, key=lambda f: (sev_rank.get(f.severity, 9), -f.impact, f.effort_minutes))[:5]

    # 3 bullets: what AI understands, biggest risk, fastest wins
    entity_ok = scores.get("entity", 0) >= 6.5
    idx_ok = scores.get("indexability", 0) >= 7.0
    cred_ok = scores.get("cred", 0) >= 6.0

    understands = []
    if entity_ok:
        understands.append("AI kan tydeligt se hvem der står bag (entity-signaler er OK).")
    else:
        understands.append("AI er usikker på hvem der står bag (entity-signaler er for svage/uklare).")

    if page_type == "Service Page":
        cov = detected.get("service_intent_coverage", {}) or {}
        cov_hits = sum(1 for _, v in cov.items() if v)
        understands.append(f"Siden virker som en service-side, men ‘købbarhed’ dækning er {cov_hits}/9.")
    else:
        understands.append("Siden ligner indhold/guide – afsender/forfatter-signaler er afgørende.")

    biggest_risk = "Største risiko: " + (top[0].title if top else "Ingen store issues fundet.")
    if not idx_ok:
        biggest_risk = "Største risiko: Indexability/AI-accessibility (robots/JS/status) kan blokere signalerne."

    wins = [f for f in top if f.effort_minutes <= 30][:3]
    win_text = "Hurtigste forbedringer: " + (", ".join([w.title for w in wins]) if wins else "Ingen oplagte quick wins.")

    return {
        "url": final_url,
        "page_type": page_type,
        "overall_score": scores.get("overall"),
        "bullets": [understands[0], understands[1], biggest_risk],
        "quick_wins": [ {"title": w.title, "effort_min": w.effort_minutes, "impact": w.impact} for w in wins ],
        "top_actions": [ {"severity": f.severity, "title": f.title, "how": f.how, "effort_min": f.effort_minutes} for f in top ],
        "win_text": win_text,
    }

def report_as_markdown(summary: Dict[str, Any], scores: Dict[str, float], findings: List[Finding]) -> str:
    lines = []
    lines.append(f"# GEO Checker – Rapport")
    lines.append("")
    lines.append(f"**URL:** {summary.get('url')}")
    lines.append(f"**Page type:** {summary.get('page_type')}")
    lines.append("")
    lines.append(f"## Scores")
    lines.append(f"- Overall: **{scores.get('overall')}/10**")
    lines.append(f"- Entity: {scores.get('entity'):.1f}/10")
    lines.append(f"- Credibility: {scores.get('cred'):.1f}/10")
    lines.append(f"- Technical: {scores.get('tech'):.1f}/10")
    lines.append(f"- Indexability: {scores.get('indexability'):.1f}/10")
    lines.append("")
    lines.append("## Sales Summary (3 bullets)")
    for b in summary.get("bullets", []):
        lines.append(f"- {b}")
    lines.append("")
    lines.append("## Top Actions")
    for a in summary.get("top_actions", [])[:5]:
        lines.append(f"- **[{a['severity']}] {a['title']}** (ca. {a['effort_min']} min)\n  - {a['how']}")
    lines.append("")
    lines.append("## Findings (prioriteret)")
    for f in findings[:20]:
        lines.append(f"- **{f.pillar} | {f.severity} | {f.title}** (Impact {f.impact}/5, {f.effort_minutes} min)\n  - {f.why}\n  - {f.how}")
    return "\n".join(lines)


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

        c_opt, c_btn = st.columns([2, 1])
        with c_opt:
            use_playwright = st.checkbox("Aktivér Playwright", value=False, help="Brug denne hvis siden blokerer bots / er JS-heavy")
            parity_check = st.checkbox("Kør Render Parity Test", value=True, help="Sammenlign tekst uden JS vs med JS ")
            crawl_trust_pages = st.checkbox("Mini-crawl trust-sider (kontakt/om/privacy)", value=True, help="Valider NAP + schema på tværs")
        with c_btn:
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
            # 1) Fetch
            if mode == "URL":
                base_fetch = fetch_url_playwright(url) if use_playwright else fetch_url_uncached(url)
                final_url = base_fetch["final_url"]
                html = base_fetch["html"]
                status = base_fetch["status"]
                headers = base_fetch["headers"]
            else:
                base_fetch = build_from_paste(pasted.strip())
                final_url = base_fetch["final_url"]
                html = base_fetch["html"]
                status = base_fetch["status"]
                headers = base_fetch["headers"]

            if not html:
                st.error("Kunne ikke hente indhold. Prøv Playwright eller tjek URL.")
                st.stop()

            # 2) Parse main page
            text, title = extract_main_text_and_title(html)
            headings = extract_headings(html)
            html_lang = extract_html_lang(html)
            hreflang = extract_hreflang(html)

            internal_links, ext_links = extract_links(html, base_url=final_url if mode == "URL" else "")
            meta = extract_meta(html)
            nap = find_nap_signals(html)
            jsonld = extract_jsonld(html)
            schema_types, schema_objs = flatten_schema_types(jsonld)
            page_type = guess_page_type(title, headings, text)

            org_obj = schema_find_org_like(schema_objs)

            # 3) Optional: render parity test
            render_parity = {"enabled": False, "ratio": 1.0, "text_len_plain": None, "text_len_js": None}
            if mode == "URL" and parity_check:
                render_parity["enabled"] = True
                plain = fetch_url_uncached(final_url)
                plain_text, _ = extract_main_text_and_title(plain.get("html") or "")
                js = fetch_url_playwright(final_url)
                js_text, _ = extract_main_text_and_title(js.get("html") or "")

                a = max(1, len(plain_text))
                b = max(1, len(js_text))
                ratio = a / b if b else 1.0
                render_parity.update({
                    "ratio": float(ratio),
                    "text_len_plain": len(plain_text),
                    "text_len_js": len(js_text),
                })

                # if user used playwright as primary, still show parity against requests
                # if user used requests primary, parity gives "js missing" visibility

            # 4) Optional: mini-crawl trust pages (contact/about/privacy)
            site_pages: Dict[str, Dict[str, Any]] = {}
            if mode == "URL" and crawl_trust_pages:
                cand = best_internal_candidates(internal_links, final_url)
                for key, u in cand.items():
                    if not u:
                        continue
                    # Keep it light: fetch via requests
                    f = fetch_url_cached(u)
                    if not f.get("html"):
                        continue
                    t, _ = extract_main_text_and_title(f["html"])
                    h = extract_headings(f["html"])
                    m = extract_meta(f["html"])
                    n = find_nap_signals(f["html"])
                    j = extract_jsonld(f["html"])
                    stypes, sobjs = flatten_schema_types(j)
                    o = schema_find_org_like(sobjs)
                    site_pages[key] = {
                        "url": f.get("final_url", u),
                        "status": f.get("status"),
                        "nap": n,
                        "meta": m,
                        "schema_types": [norm_schema_type(x) for x in stypes],
                        "org_obj": o,
                        "text_len": len(t),
                        "h1": h.get("h1", [])[:3],
                    }

            # 5) Score & findings
            overall, s_ent, s_cred, s_tech, s_idx, findings, entity_payload, detected, scores = score_and_findings(
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
                fetch_meta={
                    "status": status,
                    "final_url": final_url,
                    "headers": headers,
                    "redirect_chain": base_fetch.get("redirect_chain", []),
                },
                site_pages=site_pages,
                render_parity=render_parity,
                html_lang=html_lang,
                hreflang=hreflang,
            )

            sales_summary = build_sales_summary(final_url, page_type, scores, findings, detected)
            md_report = report_as_markdown(sales_summary, scores, findings)

        except Exception as e:
            st.error(f"Fejl under analyse: {e}")
            st.stop()

    r_space1, r_content, r_space2 = st.columns([1, 3, 1])
    with r_content:
        st.markdown("---")

        c_head1, c_head2, c_head3, c_head4 = st.columns([1.2, 1, 1.3, 1.3])

        with c_head1:
            st.markdown('<div class="css-card">', unsafe_allow_html=True)
            st.caption("PAGE TYPE")
            st.markdown(f"### {page_type}")
            if page_type == "Service Page":
                st.caption("• Fokus: Entity, Schema graph, Location, Købbarhed")
            else:
                st.caption("• Fokus: Forfatter, Expertise, Kilder")
            st.markdown('</div>', unsafe_allow_html=True)

        with c_head2:
            st.markdown('<div class="css-card" style="text-align:center;">', unsafe_allow_html=True)
            st.caption("AI READINESS")
            render_donut_score(overall)
            st.markdown('</div>', unsafe_allow_html=True)

        with c_head3:
            st.markdown('<div class="css-card">', unsafe_allow_html=True)
            st.caption("INDEXABILITY")
            st.metric("Indexability", f"{s_idx:.1f}/10")
            r = detected.get("robots_directives", {}) or {}
            pills = []
            if r.get("noindex"):
                pills.append("NOINDEX")
            if detected.get("render_parity_ratio", 1.0) < 0.65:
                pills.append("JS-HEAVY")
            if int(detected.get("status", 200)) >= 400:
                pills.append("HTTP ERROR")
            if int(detected.get("redirect_chain_len", 0)) >= 3:
                pills.append("REDIRECT CHAIN")
            if not pills:
                pills = ["OK"]
            st.markdown("".join([f"<span class='pill'>{p}</span>" for p in pills]), unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        with c_head4:
            st.markdown('<div class="css-card">', unsafe_allow_html=True)
            st.caption("EXPORT")
            st.download_button("⬇️ Download Markdown (sales)", data=md_report.encode("utf-8"), file_name="geo-report.md", mime="text/markdown", use_container_width=True)
            payload = {
                "scores": scores,
                "sales_summary": sales_summary,
                "detected": detected,
                "findings": [f.__dict__ for f in findings],
            }
            st.download_button("⬇️ Download JSON (internal)", data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), file_name="geo-report.json", mime="application/json", use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)

        # Score cards
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown('<div class="css-card" style="text-align:center;">', unsafe_allow_html=True)
            st.metric("Entity", f"{s_ent:.1f}/10")
            st.caption("Authority & Trust")
            st.markdown("</div>", unsafe_allow_html=True)
        with col2:
            st.markdown('<div class="css-card" style="text-align:center;">', unsafe_allow_html=True)
            st.metric("Credibility", f"{s_cred:.1f}/10")
            st.caption("Content & Evidence")
            st.markdown("</div>", unsafe_allow_html=True)
        with col3:
            st.markdown('<div class="css-card" style="text-align:center;">', unsafe_allow_html=True)
            st.metric("Technical", f"{s_tech:.1f}/10")
            st.caption("Schema & Meta")
            st.markdown("</div>", unsafe_allow_html=True)

        wins = quick_wins(findings)
        if wins:
            st.info(f"⚡ **{len(wins)} Quick Wins** identified! (Impact ≥4 og ≤30 min)")

        # WOW: Sales Summary
        st.subheader("💼 Sales Summary (klar til kundemøde)")
        with st.container(border=True):
            st.write(f"**URL:** {sales_summary.get('url')}")
            st.write(f"**Score:** {sales_summary.get('overall_score')}/10 • **Type:** {sales_summary.get('page_type')}")
            st.markdown("**3 key takeaways:**")
            for b in sales_summary.get("bullets", []):
                st.markdown(f"- {b}")
            if sales_summary.get("quick_wins"):
                st.markdown("**Quick wins (hurtige at eksekvere):**")
                for w in sales_summary["quick_wins"]:
                    st.markdown(f"- {w['title']} (ca. {w['effort_min']} min, impact {w['impact']}/5)")

        # Detected signals + extra site pages
        with st.expander("🔎 Detected Signals (verificér at analysen er ‘real’ – ikke generisk)"):
            st.json(detected)
            if site_pages:
                st.markdown("### Mini-crawl trust-sider")
                st.json(site_pages)

        st.subheader("📋 Detaljeret Rapport")
        tab1, tab2, tab3, tab4 = st.tabs(["🏛️ Entity Authority", "📚 Content Credibility", "⚙️ Technical Signals", "🧭 Indexability"])

        def render_findings_list(target_pillar: str):
            fs = [f for f in findings if f.pillar == target_pillar]
            if not fs:
                st.success("✅ Ingen problemer fundet.")
                return

            for f in fs:
                with st.expander(f"{f.title}"):
                    st.markdown(
                        f'<span class="badge badge-{f.severity}">{f.severity}</span> '
                        f'Impact: <b>{f.impact}/5</b> • Tid: <b>{f.effort_minutes} min</b>',
                        unsafe_allow_html=True,
                    )
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
            render_findings_list("Indexability")

        st.subheader("🕸️ Entity Relationship Map")
        with st.container(border=True):
            cm1, cm2 = st.columns([1, 3])
            with cm1:
                st.write("Visualisering af hvad AI 'ser'.")
                st.markdown("- **Solid linje:** Fundet (godt)")
                st.markdown("- **Stiplet/Rød:** Mangler (kritisk)")
            with cm2:
                render_graphviz_map(entity_payload)

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
                    "Meta": meta,
                    "Headers": headers,
                    "NAP": nap,
                }
            )