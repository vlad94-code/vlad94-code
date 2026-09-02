"""Fast deterministic local catalogue search for CNC Electric.
This module is intentionally independent of OpenAI and the network. It treats the
CNC products snapshot as a structured catalogue and searches by type/specification.
"""
from __future__ import annotations

from lexicon import resolve_category
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

PRODUCTS = Path("data/api_exports/products.json")
PRICES = Path("data/api_exports/prices.json")
STOCK = Path("data/api_exports/stock-balances.json")
TRANSIT = Path("data/api_exports/goods-in-transit.json")

class Answer(NamedTuple):
    """What answer() tells the transport.

    `wider` names a (filter key, value) that the current selection cannot
    satisfy at all — the caller offers to look for it across the whole
    catalogue, so topping out in one series does not end the conversation.

    `explains` marks the one branch (_no_such_value) that answers on the
    merits instead of listing products — what was asked, the ceiling in
    this selection, the nearest fit. A caller that redraws catalogue
    listings for a client (client_flow.py) must not touch this branch: a
    redraw from `filters` would silently replace the explanation with a
    plain listing of the standing selection. `wider` alone does not mark
    this branch — it is only set when nothing in the selection fits at
    all, not for a near-miss or a non-numeric mismatch, both of which also
    come from _no_such_value.
    """

    text: str | None
    filters: dict[str, Any]
    handled: bool
    wider: tuple[str, Any] | None = None
    explains: bool = False


@dataclass
class SearchResult:
    products: list[dict[str, Any]]
    filters: dict[str, Any]
    changed: bool

def _norm(s: Any) -> str:
    # Comma is the Russian decimal separator everywhere else in this module
    # (every numeric regex below accepts "[.,]" and _num() does
    # .replace(",", ".")) — but the character whitelist regex doesn't include
    # ",", so without converting it first, "4,5кА" became "4 5кА" (comma
    # treated as junk -> space), and the icu/current regexes then matched
    # only the "5" after the space, silently extracting the wrong number.
    s = str(s or "").lower().replace("ё","е").replace("–","-").replace("—","-").replace(",", ".")
    return re.sub(r"\s+", " ", re.sub(r"[^a-zа-я0-9./+-]+", " ", s)).strip()

def _sim(a,b): return SequenceMatcher(None,a,b).ratio()

def _fuzzy_phrase(text, phrase, threshold=.80):
    t=_norm(text).split(); p=_norm(phrase).split()
    if len(t)<len(p): return False
    for i in range(len(t)-len(p)+1):
        ok=True
        for a,b in zip(t[i:i+len(p)],p):
            if a==b: continue
            if len(b)>=5 and _sim(a,b)>=threshold: continue
            ok=False; break
        if ok:return True
    return False

def _load(path):
    if not path.exists(): return []
    try: payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return []
    if isinstance(payload,list): return payload
    if isinstance(payload,dict):
        if isinstance(payload.get("catalog"),list): return payload["catalog"]
        for v in payload.values():
            if isinstance(v,list): return v
    return []

@lru_cache(maxsize=1)
def products(): return _load(PRODUCTS)

@lru_cache(maxsize=1)
def price_map():
    return {str(x.get("vendor_code")):x for x in _load(PRICES) if isinstance(x,dict) and x.get("vendor_code")}

@lru_cache(maxsize=1)
def stock_map():
    out={}
    for x in _load(STOCK):
        if isinstance(x,dict) and x.get("vendor_code"):
            try:q=float(str(x.get("quantity","0")).replace(" ","").replace(",","."))
            except ValueError:q=0
            out.setdefault(str(x["vendor_code"]),0); out[str(x["vendor_code"])]+=q
    return out

@lru_cache(maxsize=1)
def transit_map():
    out={}
    for x in _load(TRANSIT):
        if isinstance(x,dict) and x.get("vendor_code"):
            for a in x.get("planned_arrival",[]) or []:
                try:q=float(str(a.get("quantity","0")).replace(" ","").replace(",","."))
                except ValueError:q=0
                out.setdefault(str(x["vendor_code"]),[]).append((q,str(a.get("planned_date",""))))
    return out

def clear_cache():
    # getattr-guarded so tests that swap products() for a plain lambda can call
    # this to drop everything derived from the snapshot they just replaced.
    for cached in (products, price_map, stock_map, transit_map,
                   series_matcher, indexed, by_vendor_code):
        reset = getattr(cached, "cache_clear", None)
        if reset:
            reset()


@lru_cache(maxsize=1)
def indexed():
    """(row, spec, vendor_code, series) for every product, built once per snapshot.

    filter_products() used to call spec() — which rebuilds a dict out of the
    record's specification array — for all 11942 products on every single
    query. That cost 458 ms per search, paid again for every refinement in a
    conversation. Precomputing it once takes the same work off every message.
    """
    rows = []
    for r in products():
        if isinstance(r, dict):
            rows.append((r, spec(r), str(r.get("vendor_code", "")).upper(), series(r).upper()))
    return rows


@lru_cache(maxsize=1)
def by_vendor_code():
    """Article -> product, for the O(1) lookups that would otherwise rescan."""
    return {code: row for row, _, code, _ in indexed() if code}

# A series name is a model code: Latin letters, digits, punctuation. The "Серия"
# field in 1С also carries the occasional note typed into the wrong box —
# "архив", "нужно продать", "Розетка реле", "индивид.заказ" — plus a stray
# one-character "S". Together that is 5 names over 10 products; excluding
# anything Cyrillic or shorter than two characters drops all of it and keeps
# 145 real series covering 11924 products.
_SERIES_JUNK_RE = re.compile(r"[а-я]", re.I)

# Cyrillic letters that are visually identical to a Latin one, lowercase (the
# text is already normalised). Series names are Latin codes, so a "С" typed
# from the Russian layout makes "CJX2S" silently unfindable — and it is not
# only users: CNC's own catalogue contains "Супрессор для контакторов СJX2s-M"
# with a Cyrillic С. Only true look-alikes are folded — there is no Cyrillic
# letter shaped like "n", so "НT" must not quietly become the series "NT".
# Checked against all 34506 names, descriptions and types in the snapshot:
# folding introduced zero spurious series matches.
_HOMOGLYPH_FOLD = str.maketrans("аеорсухкмтвн", "aeopcyxkmtbh")


@lru_cache(maxsize=1)
def series_matcher():
    """A matcher over the series actually present in the snapshot.

    The old hardcoded `ycm|ycw|ycb` regex left 105 of 150 series — 4692
    products, 39% of the catalogue — invisible to the search: CJX2-F, LAY5, NT,
    AD22 and even YC-prefixed YCIS8/YCCH6/YCQ7 all fell through to the raw API
    snapshot branch instead. Alternatives are ordered longest first so
    "CJX2-FN" wins over "CJX2-F" and "YCB9-80M DB" over "YCB9-80M".
    """
    names = {
        s for r in products() if isinstance(r, dict)
        for s in (series(r),)
        if len(s) >= 2 and not _SERIES_JUNK_RE.search(s)
    }
    if not names:
        return None, {}
    canonical = {_norm(s): s.upper() for s in names}
    alternatives = "|".join(
        re.escape(key) for key in sorted(canonical, key=len, reverse=True)
    )
    # Boundaries exclude letters and digits but deliberately not "-": a series
    # is written "YCB9-80M", and the hyphen has to be able to end the match.
    pattern = re.compile(r"(?<![a-zа-я0-9])(%s)(?![a-zа-я0-9])" % alternatives, re.I)
    return pattern, canonical

def spec(row):
    d={}
    for s in row.get("specification",[]) or []:
        if isinstance(s,dict) and s.get("name"):
            d[str(s["name"]).strip()]=str(s.get("value","")).strip()
    return d

def series(row):
    return str(row.get("series") or spec(row).get("Серия") or "").strip()

# CATEGORY_ALIASES удалены: используйте lexicon.resolve_category() для определения категории.
# Все синонимы категорий теперь определены в lexicon.py (CATEGORY_SYNONYMS).

def detect_category(text):
    # 1) Локальный лексикон: опечатки + синонимы.
    resolved = resolve_category(text)
    if resolved:
        types = resolved["types"]
        return next(iter(types)) if len(types) == 1 else types
    # 2) Запасной вариант: нечёткий поиск по реальным type_item из снимка.
    n = _norm(text)
    counts = {}
    for r in products():
        t = str(r.get("type_item", "")).strip()
        if len(t.split()) >= 2:
            counts[t] = counts.get(t, 0) + 1
    for t in sorted(counts, key=lambda x: (-len(x.split()), -counts[x])):
        if _fuzzy_phrase(n, t):
            return t
    return None

def _number(pattern,text):
    m=re.search(pattern,text,re.I)
    if not m:return None
    return float(m.group(1).replace(",", "."))

# Trip curve ("Характеристика срабатывания"). The snapshot only ever stores the
# Latin letters B/C/D/K/Z (plus 3 rows of "8-12In", which is a multiplier range,
# not a curve, and is not matched here). Users writing Russian reach for the
# visually identical Cyrillic В/С/К — D and Z have no Cyrillic look-alike — so
# those three fold onto their Latin twin.
_CURVE_FOLD = {"в": "b", "с": "c", "к": "k"}
_CURVE_LETTER = "[bcdkzвск]"
_CURVE_KEYWORD_RE = re.compile(
    r"(?:характеристик\w*(?:\s+срабатывани\w*)?|кривая|кривой|тип)\s*[:=]?\s*"
    + _CURVE_LETTER + r"$",
    re.I,
)
_CURVE_BARE_RE = re.compile(r"^" + _CURVE_LETTER + r"$", re.I)
_CURVE_TRAILING_RE = re.compile(r"\b" + _CURVE_LETTER + r"$", re.I)
# Any established catalogue filter licenses a curve letter; see parse_filters().
_CURVE_ANCHORS = frozenset({"type_item", "series", "current", "poles", "icu"})

# Remaining primary attributes ("основные реквизиты") of the CNC API snapshot.
K_CLASS = "Класс отключающей способности"       # H/M/N/S/L, 1440 products
K_RELEASE = "Расцепитель"                        # TMA/MA/T/A/2M/RT/3H/E/…, 1964
K_RELEASE_TYPE = "Тип расцепителя"               # Электронный/Термомагнитный/…, 5429
K_CURRENT_KIND = "Род тока"                      # AC 8809 / DC 1118
K_FRAME = "Типоразмер"                           # 62 numeric sizes, 4142
K_DISCONTINUED = "Выводится из ассортимента"     # "да" on 80 products

# Breaking-capacity class. Bare "H"/"M" would be ambiguous — they are also
# Расцепитель codes — so the letter is only read after an explicit keyword, or
# glued to the frame size as "100N". That glued form is not a guess: the product
# name spells it that way ("YCM3 E 100N 3P 100А 50кА") in 1440 of 1440 products
# that carry both attributes.
_CLASS_RE = re.compile(r"класс\w*(?:\s+отключающ\w*\s+способност\w*)?\s*[:=]?\s*([hmnsl])\b", re.I)
_CLASS_BARE_RE = re.compile(r"^[hmnsl]$", re.I)
# The lookbehind is what keeps a series out: "ycb9-80m" must NOT read as frame
# 80 + class M. Two digits minimum for the same reason short Расцепитель codes
# ("2M", "3H", "3M", "2H") must not become a frame — the smallest real
# Типоразмер is 25.
_FRAME_CLASS_RE = re.compile(r"(?<![a-zа-я0-9./+-])([0-9]{2,})\s*([hmnsl])\b", re.I)
_FRAME_RE = re.compile(r"(?:типоразмер\w*|габарит\w*|рамк\w*|frame)\s*[:=]?\s*([0-9]+)\b", re.I)
# Расцепитель codes are cryptic and short ("E", "H", "Y" collide with class
# letters), so they are only read after the word itself.
_RELEASE_RE = re.compile(r"расцепител\w*\s*[:=]?\s*([a-z0-9][a-z0-9./]*)", re.I)
_RELEASE_TYPES = ("Термомагнитный", "Электронный", "Электромеханический", "Магнитный", "Электромагнитный")
_CURRENT_KIND_RE = re.compile(r"\b(ac|dc)\b|\b(постоянн\w*|переменн\w*)\s+ток\w*", re.I)

# Every filter key parse_filters() can produce, in one place so answer() and the
# engine adapters cannot drift apart from it.
FILTER_KEYS = (
    "type_item", "series", "article", "current", "poles", "icu", "curve",
    "release_class", "release", "release_type", "current_kind", "frame",
)


def parse_filters(text, prior=None, *, anchors=()):
    """`anchors` names filters already established earlier in the conversation.
    They only license otherwise-ambiguous parses (currently the trip curve) and
    are deliberately NOT merged into the result, so callers can still tell what
    this message contributed on its own."""
    n=_norm(text)
    f=dict(prior or {})
    # Numeric filters THIS message established, as opposed to ones merged in
    # from `prior`. The trailing-curve shape below needs that distinction.
    own=set()
    cat=detect_category(n)
    if cat: f["type_item"]=cat

    # Series first: a name that really exists in the snapshot is a far stronger
    # signal than the speculative article shape below. "D11" fits both, and as
    # an article it searched for a vendor_code that does not exist and returned
    # nothing at all.
    pattern, canonical = series_matcher()
    matched_series = None
    if pattern:
        # str.translate is length-preserving, so a match found in the folded
        # copy has the same span in `n` — which the article check below relies on.
        matched_series = pattern.search(n) or pattern.search(n.translate(_HOMOGLYPH_FOLD))
    if matched_series:
        f["series"] = canonical[matched_series.group(1).lower()]

    m=re.search(r'\b([a-z]\d{2,}[a-z0-9._/-]*)\b',n,re.I)
    if m and not (matched_series and matched_series.span() == m.span()):
        f["article"]=m.group(1).upper()
    # Character class must include "-": most real YCB series are hyphenated
    # (YCB6H-63, YCB7-63N, YCB9-80M...) — without it this stopped at the
    # hyphen (e.g. "YCB6H-63" -> "YCB6H"), a truncated series that never
    # equality-matches series()'s full "YCB6H-63" in filter_products(), so
    # a search for the exact real series silently returned no results.
    # Fallback for when the snapshot is missing or has not caught up with a
    # newly introduced series.
    if "series" not in f:
        m=re.search(r'\b(ycm[0-9a-z-]+|ycw[0-9a-z-]+|ycb[0-9a-z-]+)\b',n,re.I)
        if m: f["series"]=m.group(1).upper().rstrip("-")

    # Current: only phrases with A/А/ампер, or "на 1600" after a product context.
    m=re.search(r'(?:на|\bток(?:ом)?\b|in\s*=?|номинальн\w*\s*ток\w*)\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:а|a|ампер(?:а|ов)?\b)',n,re.I)
    if not m:
        m=re.search(r'\b([0-9]+(?:[.,][0-9]+)?)\s*(?:а|a|ампер(?:а|ов)?\b)',n,re.I)
    if m: f["current"]=float(m.group(1).replace(",", ".")); own.add("current")

    # If there is an established equipment context, "на 1600" means current.
    if "current" not in f and ("type_item" in f or "series" in f):
        m=re.search(r'(?:на|\bток(?:ом)?\b|in\s*=?|номинальн\w*\s*ток\w*)\s*([0-9]+(?:[.,][0-9]+)?)\b',n,re.I)
        if m: f["current"]=float(m.group(1).replace(",", ".")); own.add("current")

    # The class covers the Cyrillic look-alike "р" and the Russian abbreviation
    # "п" ("полюс") next to Latin "p": users answering the bot's own «Уточните
    # параметры: например, «3P»» prompt type it from the Russian keyboard
    # layout. Without them "3р"/"3п" parsed to an empty filter set, so answer()
    # saw no fresh keys, returned handled=False, and the refinement fell through
    # to RAG instead of narrowing the carried-over catalog_filters. The trailing
    # \b still keeps "3 рубля"/"2 позиции" out.
    m=re.search(r'\b([1-4])\s*[pрп]\b',n,re.I)
    if m: f["poles"]=m.group(1)+"P"; own.add("poles")
    # "3 полюса"
    m=re.search(r'\b([1-4])\s*(?:полюс|полюса|полюсов)\b',n,re.I)
    if m: f["poles"]=m.group(1)+"P"; own.add("poles")

    # kA is Icu. Avoid treating 1600A as kA. Both letters must accept either
    # alphabet: "к"/"k" and "а"/"a" are indistinguishable on screen, and the
    # unit gets typed in every mix. The old class covered only a Cyrillic "к",
    # so "6kA" parsed to nothing at all — answer() then saw no fresh keys,
    # reported handled=False and the refinement fell through to RAG, exactly
    # like the poles and trip-curve cases before it. Requiring both letters
    # still keeps a bare "1600A" out.
    m=re.search(r'(?:icu|отключающ\w*(?:ая|ой)?\s*способн\w*|кз|коротк\w* замык)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*[кk][аa]\b',n,re.I)
    if not m:
        m=re.search(r'\b([0-9]+(?:[.,][0-9]+)?)\s*[кk][аa]\b',n,re.I)
    if m: f["icu"]=float(m.group(1).replace(",", ".")); own.add("icu")

    # A curve letter is only trusted when some catalogue filter is already
    # established — in this message or carried over from the conversation via
    # `anchors`. A bare letter in free text is too ambiguous on its own (see
    # the К-2 backlog fix: a similar catch-all was removed from knowledge_v2.py
    # for fabricating facts out of stray standalone letters).
    if _CURVE_ANCHORS & (f.keys() | set(anchors)):
        # Unambiguous by shape: an explicit keyword ("характеристика С",
        # "кривая D"), or a message that is nothing but the letter — the usual
        # reply to the bot's own «Уточните параметры» prompt.
        m = _CURVE_KEYWORD_RE.search(n) or _CURVE_BARE_RE.match(n)
        # A letter merely trailing a longer message stays gated on a numeric
        # filter from THIS message ("...4,5кА C", the way the real product name
        # spells it). Widening this shape to a carried-over anchor would let a
        # truncated sentence ending in the preposition "с"/"в"/"к" silently
        # become a curve.
        if m is None and own:
            m = _CURVE_TRAILING_RE.search(n)
        if m:
            letter = m.group(0)[-1].lower()
            f["curve"] = _CURVE_FOLD.get(letter, letter).upper()

    # Frame size + breaking-capacity class. The glued "250N" form carries both
    # at once and is unambiguous, so it also settles the number: a bare "250"
    # stays the nominal current (the two attributes share 28 values — 25, 63,
    # 100, 250, 400, 630, 800…— and current is what people ask for).
    m = _FRAME_CLASS_RE.search(n)
    if m:
        f["frame"] = float(m.group(1))
        f["release_class"] = m.group(2).upper()
    m = _FRAME_RE.search(n)
    if m: f["frame"] = float(m.group(1))
    m = _CLASS_RE.search(n)
    if m: f["release_class"] = m.group(1).upper()
    # A message that is nothing but a letter, answering the bot's own prompt.
    # Class values (H/M/N/S/L) and curve values (B/C/D/K/Z) share no letter, so
    # a bare one resolves without ambiguity — the keyword was over-cautious and
    # left "N" answering "Уточните… «класс N»" falling through to RAG. Folded
    # first, since Cyrillic Н and М are the Latin H and M on screen.
    if _CURVE_ANCHORS & (f.keys() | set(anchors)):
        m = _CLASS_BARE_RE.match(n.translate(_HOMOGLYPH_FOLD))
        if m: f["release_class"] = m.group(0).upper()

    m = _RELEASE_RE.search(n)
    if m: f["release"] = m.group(1).upper()

    for value in _RELEASE_TYPES:
        if _norm(value) in n:
            f["release_type"] = value
            break

    m = _CURRENT_KIND_RE.search(n)
    if m:
        if m.group(1): f["current_kind"] = m.group(1).upper()
        else: f["current_kind"] = "DC" if m.group(2).startswith("постоянн") else "AC"

    return f

def _num(v):
    try:return float(str(v).replace(" ","").replace(",","."))
    except ValueError:return None

# Icu is spread over several spec keys in the API snapshot. 6491 products carry
# the plain key below; 70 more ("Воздушный выкл. без расцепителя") carry only
# voltage-qualified variants ("Номин. предельная отключающая способность Icu при
# напряжении 400/415В (kA)"), which made them unmatchable by an Icu filter at
# all. Note the unit suffix is spelled "(кА)" on some keys and "(kA)" on others —
# the same Cyrillic/Latin mix users type — so both are accepted here too.
_ICU_PLAIN = "Номин. отключающая способность Icu (кА)"


def _icu_values(s):
    """Every Icu figure a product answers to. The plain key wins when present;
    otherwise all voltage-qualified ones count, since a frame rated 135 kA at
    400 V and 100 kA at 690 V genuinely has both capabilities."""
    if _ICU_PLAIN in s:
        return [_num(s[_ICU_PLAIN])]
    return [
        _num(v) for k, v in s.items()
        if "icu" in k.lower() and ("(ка)" in k.lower() or "(ka)" in k.lower())
    ]


def filter_products(filters):
    out=[]
    for r, s, code, row_series in indexed():
        if filters.get("article") and code!=filters["article"]: continue
        wanted_type = filters.get("type_item")
        if wanted_type:
            actual_type = str(r.get("type_item", "")).strip()
            if isinstance(wanted_type, (set, frozenset)):
                if actual_type not in wanted_type:
                    continue
            elif actual_type != wanted_type:
                continue
        if filters.get("series") and row_series!=filters["series"]: continue
        if filters.get("current") is not None and _num(s.get("Номинальный ток In (А)"))!=filters["current"]: continue
        if filters.get("poles") and s.get("Количество полюсов","").upper()!=filters["poles"]: continue
        if filters.get("icu") is not None and filters["icu"] not in _icu_values(s): continue
        if filters.get("curve") and s.get("Характеристика срабатывания","").upper()!=filters["curve"]: continue
        if filters.get("release_class") and s.get(K_CLASS,"").upper()!=filters["release_class"]: continue
        if filters.get("release") and s.get(K_RELEASE,"").upper()!=filters["release"]: continue
        if filters.get("release_type") and s.get(K_RELEASE_TYPE,"")!=filters["release_type"]: continue
        if filters.get("current_kind") and s.get(K_CURRENT_KIND,"").upper()!=filters["current_kind"]: continue
        if filters.get("frame") is not None and _num(s.get(K_FRAME))!=filters["frame"]: continue
        out.append(r)
    return out


def sole_product(filters, results=None):
    """The single product these filters select, else None.

    Only a single match earns the accessory question: with a list of candidates
    on screen there is no one product to offer accessories for. `results` lets
    a caller that has already filtered pass them in — filter_products() walks
    the whole catalogue, and this must not double the cost of a search.
    """
    if results is None:
        results = filter_products(filters)
    return results[0] if len(results) == 1 else None


def discontinued(row):
    """The factory no longer makes this item — it can still be sold, but only
    out of whatever is left in stock, so it is flagged rather than hidden."""
    return str(spec(row).get(K_DISCONTINUED, "")).strip().lower() == "да"

def _fmt_num(x):
    return str(int(x)) if float(x).is_integer() else str(x).replace(".",",")


#: Public names for callers outside this module (bot.py renders filter values,
#: pricelist_store compares numbers declared in the price list).
format_number = _fmt_num
to_number = _num

def _operational(code):
    p=price_map().get(code,{})
    price=p.get("base_price")
    stock=stock_map().get(code,0)
    transit=transit_map().get(code,[])
    return price,stock,transit

def display_name(row):
    """The product name without the leading type, which the card already shows
    on its own line — "Модульный автоматический выключатель YCB6H-63 10А 1P"
    becomes "YCB6H-63 10А 1P". 81% of names start with their type_item; the
    rest (e.g. type "Высоковольтный предохранитель", name "Предохранитель
    XRNT-12/…") are left whole rather than mangled."""
    name = str(row.get("name", "")).strip()
    kind = str(row.get("type_item", "")).strip()
    if kind and name.lower().startswith(kind.lower()):
        trimmed = name[len(kind):].strip(" -—,")
        if trimmed:
            return trimmed
    return name


def _fmt_date(value):
    parts = str(value or "").split("-")
    return ".".join(reversed(parts)) if len(parts) == 3 else str(value or "")


# Что видит клиент вместо склада. Клиент — подписчик канала, а не
# сотрудник: цифра остатка и дата прихода это внутренняя коммерческая
# информация, и забронировать по ней клиент всё равно ничего не может.
STOCK_FOR_CLIENT = "уточните наличие у менеджера"


def availability(row, short=False, show_stock=True):
    """Stock in plain words. A bare "0 шт." would be the answer for 88% of the
    catalogue (only 1383 of 11942 items are in stock) and reads as "we can't
    sell you this", when in fact the factory still makes almost all of them.
    The K_DISCONTINUED attribute is what separates "под заказ" from a dead end."""
    if not show_stock:
        return STOCK_FOR_CLIENT
    code = str(row.get("vendor_code", ""))
    stock = stock_map().get(code, 0)
    transit = transit_map().get(code, [])
    if stock:
        return f"{_fmt_num(stock)} шт."
    if transit:
        qty = _fmt_num(sum(q for q, _ in transit))
        if short:
            return f"ожидается {qty} шт."
        dates = [d for _, d in transit if d]
        when = f" к {_fmt_date(min(dates))}" if dates else ""
        return f"на складе нет, ожидается {qty} шт.{when}"
    if discontinued(row):
        return "нет на складе"
    return "под заказ"


def price_line(code):
    price = price_map().get(code, {}).get("base_price")
    return f"{price} р." if price is not None else "нет в прайсе"


# Primary attributes first and in a fixed order, so the same fields sit in the
# same place on every card; everything else the item carries follows.
_CARD_SPEC_ORDER = (
    # Типоразмер sits right after Серия: together they identify the frame the
    # item belongs to ("YCM3 100N"), which is what a manager reads first —
    # the nominal current only makes sense inside a given frame. Items without
    # the attribute (65% of the catalogue) simply skip the line.
    "Серия", K_FRAME, "Номинальный ток In (А)", "Количество полюсов",
    "Характеристика срабатывания", _ICU_PLAIN,
    K_CLASS, K_RELEASE, K_RELEASE_TYPE, K_CURRENT_KIND,
)


def card(row, show_stock=True):
    """The full single-product card. Shown only when the search narrows to one
    item — at 472+ characters apiece, a page of these blows past Telegram's
    4096-character message limit after about eight."""
    code = str(row.get("vendor_code", ""))
    s = spec(row)
    lines = [
        f"Артикул: {code}",
        f"Тип: {row.get('type_item', '')}",
        f"Наименование: {display_name(row)}",
        f"Тарифная цена: {price_line(code)}",
        f"Наличие на складе: {availability(row, show_stock=show_stock)}",
    ]
    if discontinued(row):
        lines.append("⚠️ Снято с производства — продаётся только из складских остатков")
    shown = {K_DISCONTINUED}
    body = []
    for key in _CARD_SPEC_ORDER:
        if s.get(key):
            body.append(f"{key}: {s[key]}")
            shown.add(key)
    body += [f"{k}: {v}" for k, v in s.items() if k not in shown and v]
    if body:
        lines += [""] + body
    if row.get("item_description"):
        lines += ["", f"Описание: {row['item_description']}"]
    return "\n".join(lines)


# Filter key -> the spec field it reads, so a hint can name what to say next.
FILTER_SPEC_KEYS = {
    "frame": K_FRAME,
    "current": "Номинальный ток In (А)",
    "poles": "Количество полюсов",
    "curve": "Характеристика срабатывания",
    "icu": _ICU_PLAIN,
    "release_class": K_CLASS,
    "release": K_RELEASE,
    "release_type": K_RELEASE_TYPE,
    "current_kind": K_CURRENT_KIND,
}
_HINT_ORDER = ("Серия",) + tuple(_CARD_SPEC_ORDER[1:])


def varying(results, limit=6):
    """Which attributes still differ across these results, and their values.

    The old hint was a fixed sentence, so it kept suggesting «3P» and «50 кА»
    after those were already chosen and never mentioned the типоразмер and
    расцепитель the remaining items actually differed by. A client who does not
    know the range cannot guess it — the search has to offer what exists.
    """
    seen = {}
    for row in results:
        s = spec(row)
        for key in _HINT_ORDER:
            value = s.get(key)
            if value:
                seen.setdefault(key, {}).setdefault(value, 0)
                seen[key][value] += 1
    out = []
    for key in _HINT_ORDER:
        counts = seen.get(key)
        if not counts or len(counts) < 2:
            continue
        values = sorted(counts, key=lambda v: -counts[v])
        shown = ", ".join(values[:limit]) + ("…" if len(values) > limit else "")
        out.append((key, shown))
    return out


# Filters compared as numbers by filter_products(); the rest as upper-case text,
# except release_type, whose values are whole Russian words stored verbatim.
_NUMERIC_FILTERS = frozenset({"current", "icu", "frame"})
_VERBATIM_FILTERS = frozenset({"release_type"})


def match_offered(text, prior):
    """Resolve a bare reply against the values the current selection offers.

    The client is choosing from what the bot listed, not composing a query, so
    the answer is whatever attribute actually carries that value here. Only an
    unambiguous single hit counts — and only when a selection is already
    running, so stray words never become filters.
    """
    if not prior:
        return None
    needle = _norm(text)
    if not needle:
        return None
    results = filter_products(prior)
    if not results:
        return None
    hits = {}
    for key, spec_key in FILTER_SPEC_KEYS.items():
        if key in prior:
            continue
        for row in results:
            value = spec(row).get(spec_key)
            if value and _norm(value) == needle:
                if key in _NUMERIC_FILTERS:
                    hits[key] = _num(value)
                elif key in _VERBATIM_FILTERS:
                    hits[key] = value
                else:
                    hits[key] = value.upper()
                break
    return hits if len(hits) == 1 else None


def refine_hint(results):
    rows = varying(results)
    if not rows:
        return ["", "Оставшиеся позиции совпадают по основным параметрам — выберите артикул."]
    return ["", "Что ещё различается — назовите значение:"] + [f"• {key}: {shown}" for key, shown in rows]


def result_text(results, filters, show_stock=True):
    if not results:
        return "По текущему каталогу подходящих товаров не найдено. Попробуйте изменить один из параметров."
    if len(results)==1:
        return card(results[0], show_stock=show_stock)
    if len(results)>30:
        by={}
        for r in results: by[series(r) or "Без серии"]=by.get(series(r) or "Без серии",0)+1
        lines=[f"Найдено {len(results)} позиций — уточните серию, номинальный ток или характеристику.", "", "Серии:"]
        lines += [f"• {k} — {v}" for k,v in sorted(by.items(), key=lambda x:(-x[1],x[0]))]
        lines += refine_hint(results)
        return "\n".join(lines)
    # One line per item — article, name, price, stock — so a manager can scan
    # candidates and pick one. The full card comes once the choice is made.
    lines=[f"Найдено {len(results)} позиций.", ""]
    for r in results[:15]:
        code=str(r.get("vendor_code",""))
        row=f"• {code} · {display_name(r)} · {price_line(code)} · {availability(r, short=True, show_stock=show_stock)}"
        if discontinued(r): row += " ⚠️"
        lines.append(row)
    if len(results)>15: lines.append(f"…и ещё {len(results)-15} позиций.")
    lines += refine_hint(results)
    return "\n".join(lines)

def answer(question, prior=None):
    prior = prior or {}
    # Только то, что реально нашлось В ЭТОМ сообщении (без учёта прошлых
    # фильтров разговора). Если тут пусто — не считаем это уточнением
    # предыдущего поиска, иначе бот молча повторит старый ответ на
    # совершенно другой вопрос (например, если раньше искали "контакторы",
    # а теперь спросили конкретный артикул, которого движок сам не узнал).
    # Пусть в этом случае попробуют другие движки — точный поиск по
    # артикулу или свободный вопрос.
    # `prior` is passed as `anchors`, not as `prior`: the conversation's
    # established filters must be allowed to license an ambiguous parse (a bare
    # curve letter) without being merged in and counted as this message's own
    # contribution. Before this, a curve-only refinement — "C", even in Latin —
    # parsed to nothing here, reported handled=False and fell through to RAG,
    # which made the gate's own "or carried over" promise unreachable.
    fresh = parse_filters(question, None, anchors=prior.keys())
    fresh_keys = {k for k in FILTER_KEYS if k in fresh}
    if not fresh_keys:
        # Whatever the hint offered, the client must be able to type back. Every
        # attribute otherwise needs its own keyword rule, and each one missed
        # ends the conversation on a value the bot itself had just listed
        # ("Расцепитель: E, T/A, Y…" then "E" fell through to RAG).
        offered = match_offered(question, prior)
        if offered:
            fresh, fresh_keys = offered, set(offered)
        else:
            return Answer(None, prior, False)
    # A fresh article, or a series that differs from the one already in
    # `prior`, means the user pivoted to a new/unrelated product — merging
    # onto `prior` in that case ANDs in stale current/poles/icu/curve from
    # an earlier, unrelated question and produces an impossible combined
    # filter that always matches 0 rows (e.g. asking "B000001" right after
    # "YCB6H-63 10А 1P 4,5кА C" inherited that series+current+poles+icu and
    # searched for all of them at once, even though B000001 alone exists).
    # Only merge as a refinement of the SAME ongoing search — no article,
    # and no series or the same series as before.
    pivoted = "article" in fresh or ("series" in fresh and fresh.get("series") != prior.get("series"))
    filters = fresh if pivoted else {**prior, **fresh}
    results = filter_products(filters)
    if not results and not pivoted and prior:
        # The refinement emptied an otherwise live selection. Applying it would
        # end the conversation for good: every later refinement ANDs onto an
        # impossible filter and also returns nothing, so a client who typed a
        # current that does not exist ("110A") could never recover. Keep the
        # last working selection and answer with what IS on offer.
        standing = filter_products(prior)
        if standing:
            text, unmet = _no_such_value(fresh, standing)
            return Answer(text, prior, True, unmet, explains=True)
    # No article special-case any more: result_text() renders the full card for
    # any single match, however the search narrowed to it. That used to be
    # needed because the multi-result summary hardcoded seven breaker-oriented
    # fields and showed almost nothing for a contactor.
    return Answer(result_text(results, filters), filters, True)


def _fmt_filter_value(value):
    return _fmt_num(value) if isinstance(value, float) else str(value)


# Trade rules for the numeric attributes, from the customer's own practice.
# A breaker rated below the current the client asked for must never be offered:
# 25 А is not an answer to "30 А". Breaking capacity tolerates ±10 kA, and only
# when nothing lands inside that window does the search go strictly upwards.
_NUMERIC_TOLERANCE = {"current": 0.0, "icu": 10.0}


def _suitable(values, wanted, tolerance):
    if tolerance:
        # Inside the window, order by distance from what was asked, and prefer
        # the higher of two equals: answering "15 кА" with 6 when 10 is on the
        # shelf is both further off and the weaker breaking capacity.
        near = sorted((v for v in values if abs(v - wanted) <= tolerance),
                      key=lambda v: (abs(v - wanted), -v))
        if near:
            return near
    return sorted(v for v in values if v > wanted)


def _no_such_value(fresh, standing):
    """Name what the client asked for, then what can actually be sold instead.

    Returns the reply and, when nothing in this selection can satisfy the
    request at all, the (key, value) worth searching for catalogue-wide — the
    conversation must not end just because one series tops out too low.
    """
    offered = dict(varying(standing, limit=12))
    lines, unmet = [], None
    for key, wanted in fresh.items():
        spec_key = FILTER_SPEC_KEYS.get(key)
        if not spec_key:
            continue
        shown = _fmt_filter_value(wanted)
        if key in _NUMERIC_TOLERANCE:
            values = {v for r in standing for v in (_num(spec(r).get(spec_key)),) if v is not None}
            fits = _suitable(values, wanted, _NUMERIC_TOLERANCE[key])
            if not fits:
                ceiling = _fmt_num(max(values)) if values else "—"
                lines.append(f"• {spec_key}: {shown} — в этой выборке такого нет, максимум {ceiling}.")
                unmet = (key, wanted)
                continue
            lines.append(f"• {spec_key}: {shown} — точно такого нет.")
            lines.append(f"  Ближайший подходящий: {_fmt_num(fits[0])}")
            if len(fits) > 1:
                lines.append(f"  Подходят: {', '.join(_fmt_num(v) for v in fits)}")
            continue
        lines.append(f"• {spec_key}: {shown} — такого в этой выборке нет.")
        if spec_key in offered:
            lines.append(f"  Есть: {offered[spec_key]}")
        else:
            values = sorted({spec(r).get(spec_key) for r in standing if spec(r).get(spec_key)})
            if values:
                lines.append(f"  Есть только: {', '.join(values)}")
    if not lines:
        lines = ["По этому уточнению ничего не нашлось."]
    text = "\n".join(
        lines + ["", f"Оставляю прошлый отбор — {len(standing)} поз."] + refine_hint(standing)
    )
    return text, unmet

def detail(code, show_stock=True):
    for r in products():
        if str(r.get("vendor_code","")).upper()==code.upper():
            return card(r, show_stock=show_stock)
    return "Товар с таким артикулом не найден."


# Тот же разбор, что у ProductDetailEngine (engines/adapters.py): слово от
# четырёх знаков, с цифрой, начинающееся с буквы серии. Клиенту движки
# закрыты, а точный артикул — нет (ARCHITECTURE.md §3), поэтому bot.py
# разбирает его сам, и эти два разбора не должны разойтись.
_ARTICLE_RE = re.compile(
    r"(?:артикул\s*[:№#-]?\s*)?([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/-]{3,})",
    re.I,
)
_ARTICLE_PREFIXES = ("B", "A", "C", "D", "E", "F", "G", "S", "Y")


def article_code(text):
    """Артикул из сообщения — или None, если это свободный вопрос.

    Есть ли такой товар в каталоге, здесь не проверяется: это дело detail().
    """
    match = _ARTICLE_RE.search(text or "")
    if not match or not any(c.isdigit() for c in match.group(1)):
        return None
    code = match.group(1).upper()
    return code if code.startswith(_ARTICLE_PREFIXES) else None


def first_article(text):
    m=re.search(r'\b([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/-]{3,})\b',text)
    return m.group(1).upper() if m else None