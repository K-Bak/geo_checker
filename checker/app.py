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
    .stApp { background-color: #f8f9fa; }

    /* Card Styling */
    .css-card {
        background-color: #ffffff;
        border-radius: 12px;
        padding: 24px;
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

def fetch_url_playwright(url: str) -> Tuple[str, str, int, Dict[str, str]]:
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
            return url, "", 0, {}
        finally:
            context.close()
            browser.close()

    return final_url, html or "", status, headers

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
    # Hvis vi har mange eksterne citations, antag at de kan være dokumenteret
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
) -> Tuple[float, float, float, float, List[Finding], Dict[str, Any], Dict[str, Any]]:
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
    has_review_schema = ("Review" in clean_schema_types) or ("AggregateRating" in clean_schema_types)
    reviews_mentioned = ("trustpilot" in text.lower()) or ("anmeldelse" in text.lower()) or ("stjerner" in text.lower())

    has_h1 = len(headings.get("h1", [])) > 0
    h2_count = len(headings.get("h2", []))

    # --- 2) New “page reality” signals ---
    word_count = len(text.split())
    meta_desc = (meta.get("description") or "").strip()
    has_canonical = bool((meta.get("canonical") or "").strip())

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

    # Evidence links quality (simple)
    high_trust_domains = ("mst.dk", "miljo", "miljø", "ds.dk", "iso.org", "ecolabel", "svanemaerket", "svanemærket", "sikkerhedsdatablad", "sds")
    trusted_out_links = [u for u in ext_links if any(k in u.lower() for k in high_trust_domains)]

    # NAP presence
    nap_phone = bool(nap.get("phone"))
    nap_email = bool(nap.get("email"))
    nap_address = bool(nap.get("address"))
    nap_cvr = bool(nap.get("cvr"))

    # --- 3) Scoring ---
    # Entity Authority (0-10)
    entity_score = 0.0
    if person_obj or author_visible:
        entity_score += 2.5
    if org_obj:
        entity_score += 3.0
    if nap_phone or nap_email:
        entity_score += 1.0
    if nap_address:
        entity_score += 1.0
    if nap_cvr:
        entity_score += 1.0
    if len(socials) >= 2:
        entity_score += 1.5
    elif len(socials) == 1:
        entity_score += 0.8
    if has_about:
        entity_score += 0.8
    if has_contact:
        entity_score += 0.8

    entity_score = clamp(entity_score, 0, 10)

    # Content Credibility (0-10)
    cred_score = 0.0
    if external_citations >= 3:
        cred_score += 2.5
    elif external_citations >= 1:
        cred_score += 1.0

    # Don't reward claims; punish if claims exist and no citations
    if unsourced_claims and external_citations == 0:
        cred_score -= 1.0

    if len(trusted_out_links) >= 1:
        cred_score += 1.5

    # Expert quote heuristic (basic)
    has_expert_quotes = bool(re.search(r"\b(siger|udtaler|ifølge|citat|kilde:)\b", text, re.I))
    if has_expert_quotes:
        cred_score += 1.5

    # Service-page helpfulness
    if page_type == "Service Page" and has_process:
        cred_score += 1.0
    if page_type == "Service Page" and has_pricing:
        cred_score += 1.0
    if h2_count >= 3:
        cred_score += 0.5

    # Guarantee only helps if terms exist
    if has_guarantee and has_terms:
        cred_score += 0.5

    # Thin content penalty
    if word_count < 450:
        cred_score -= 1.0
    elif word_count < 700:
        cred_score -= 0.3

    cred_score = clamp(cred_score, 0, 10)

    # Technical Signals (0-10)
    tech_score = 0.0
    if clean_schema_types:
        tech_score += 2.0
    if page_type == "Service Page" and "Service" in clean_schema_types:
        tech_score += 2.5
    if ("Organization" in clean_schema_types) or ("LocalBusiness" in clean_schema_types):
        tech_score += 2.0
    if reviews_mentioned and has_review_schema:
        tech_score += 1.5
    if has_h1:
        tech_score += 1.0
    if meta_desc:
        tech_score += 0.5
    if has_canonical:
        tech_score += 0.5
    if has_privacy:
        tech_score += 0.5

    tech_score = clamp(tech_score, 0, 10)

    overall = round(0.35 * entity_score + 0.35 * cred_score + 0.30 * tech_score, 1)

    # --- 4) Findings (more specific, less repetitive) ---
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

    if page_type == "Service Page" and not has_pricing:
        findings.append(Finding(
            "Content Credibility", "Medium",
            "Ingen pris-/fra-pris signaler",
            "Uden prisindikatorer bliver siden mere 'brochure' end købbar ydelse.",
            "Tilføj 'fra-pris', priseksempler eller hvad der påvirker prisen (areal, tilstand, adgang, osv.).",
            3, 20,
            evidence="Ingen 'pris/fra xx kr/dkk/kr.' fundet i tekst."
        ))

    if page_type == "Service Page" and not has_process:
        findings.append(Finding(
            "Content Credibility", "Medium",
            "Proces/arbejdsgang ikke tydelig",
            "AI forstår og stoler mere på ydelser med konkret metode og trin.",
            "Tilføj 3–6 trin: forberedelse → udførelse → efterbehandling + tid/forbehold.",
            3, 25,
            evidence="Ingen proces-/trin-signal fundet (proces/trin/step/sådan foregår)."
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

    if not has_expert_quotes:
        findings.append(Finding(
            "Content Credibility", "Medium",
            "Ingen ekspertudtalelser/kilder fundet",
            "AI vægter indhold højere, når det understøttes af fagpersoner eller dokumenterede kilder.",
            "Indsæt 1–2 korte citater med attribution + link til kilde (producent, myndighed, standard, SDS).",
            3, 30,
            evidence="Ingen 'ifølge/siger/udtaler/citat/kilde:' fundet."
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

    if page_type == "Service Page" and not has_service_area:
        findings.append(Finding(
            "Content Credibility", "Medium",
            "Serviceområde ikke tydeligt",
            "AI kan ikke matche ydelsen til geografi uden klare område-signaler.",
            "Tilføj byer/regioner eller 'Hele Danmark' + evt. liste over områder.",
            3, 15,
            evidence="Ingen 'vi kører/dækker/område/Hele Danmark' signal fundet."
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

    if page_type == "Service Page" and has_faq_like and "FAQPage" not in clean_schema_types:
        findings.append(Finding(
            "Technical Signals", "Medium",
            "FAQ-indhold uden FAQPage schema",
            "Hvis du allerede har FAQ-lignende indhold, kan schema give bedre udtræk til AI.",
            "Markér Q&A som FAQPage schema.",
            3, 25,
            evidence="FAQ-signaler fundet (FAQ/spørgsmål/?) men ingen FAQPage schema."
        ))

    if not has_privacy:
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

    # --- 5) Entity map ---
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
    }

    return overall, entity_score, cred_score, tech_score, findings, entity_payload, detected


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
            use_playwright = st.checkbox("Aktivér Playwright", value=False, help="Brug denne hvis siden blokerer bots")
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
            if mode == "URL":
                final_url, html, status, _headers = (
                    fetch_url_playwright(url) if use_playwright else fetch_url_uncached(url)
                )
            else:
                final_url, html, status, _headers = build_from_paste(pasted.strip())

            if not html:
                st.error("Kunne ikke hente indhold. Prøv Playwright eller tjek URL.")
                st.stop()

            text, title = extract_main_text_and_title(html)
            headings = extract_headings(html)
            internal_links, ext_links = extract_links(html, base_url=final_url if mode == "URL" else "")
            meta = extract_meta(html)
            nap = find_nap_signals(html)

            jsonld = extract_jsonld(html)
            schema_types, schema_objs = flatten_schema_types(jsonld)
            page_type = guess_page_type(title, headings, text)

            overall, s_ent, s_cred, s_tech, findings, entity_payload, detected = score_and_findings(
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
            )
        except Exception as e:
            st.error(f"Fejl under analyse: {e}")
            st.stop()

    r_space1, r_content, r_space2 = st.columns([1, 3, 1])
    with r_content:
        st.markdown("---")

        c_head1, c_head2, c_head3 = st.columns([1.2, 1, 1.5])

        with c_head1:
            st.markdown('<div class="css-card">', unsafe_allow_html=True)
            st.caption("PAGE TYPE")
            st.markdown(f"### {page_type}")
            if page_type == "Service Page":
                st.caption("• Focus: Service Provider Entity, Schema Markup, Location, Purchase Signals")
            else:
                st.caption("• Focus: Author Authority, Expertise, Citations")
            st.markdown('</div>', unsafe_allow_html=True)

        with c_head2:
            st.markdown('<div class="css-card" style="text-align:center;">', unsafe_allow_html=True)
            st.caption("AI READINESS")
            render_donut_score(overall)
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
        if wins:
            st.info(f"⚡ **{len(wins)} Quick Wins** identified! See details below.")
        
        # Detected Signals (so you can verify the analysis is not generic)
        with st.expander("🔎 Detected Signals"):
            st.json(detected)

        st.subheader("📋 Detaljeret Rapport")
        tab1, tab2, tab3 = st.tabs(["🏛️ Entity Authority", "📚 Content Credibility", "⚙️ Technical Signals"])

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
                    "NAP": nap,
                }
            )