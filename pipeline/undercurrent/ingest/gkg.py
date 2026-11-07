"""GDELT GKG rows -> the `articles` table (docs/WORKPLAN.md 3.1) and daily totals.

Independent of where the rows come from: BigQuery (gdelt_bq.py) or, later, the 15-minute zip
files. Input is one DataFrame of raw rows with these columns (GKG field names in brackets):

    record_id         [GKGRECORDID]      translated-feed ids contain "-T"
    date_int          [DATE]             YYYYMMDDHHMMSS, UTC
    domain            [SourceCommonName]
    url               [DocumentIdentifier]
    title             <PAGE_TITLE> from [Extras], still HTML-escaped; may be missing
    locations         [Locations]        V1: type#name#FIPS#adm1#lat#lon#featureid;...
    persons           [Persons]          a;b;c
    orgs              [Organizations]    a;b;c
    themes            [Themes]           V1 themes, a;b;c;
    tone              [V2Tone]           tone,positive,negative,...
    translation_info  [TranslationInfo]  srclc:fra;eng:...; empty in the English feed

Per day this keeps one row per distinct headline: duplicate URLs go first, then syndicated copies
(the same normalised title on other sites) fold into the earliest one, with `dup_count` saying
how many distinct URLs it stands for. Rows without a title are dropped, as the vector store can't
embed them. The daily totals are counted *before* any of that, so outages and source mix show up.
"""

import datetime as dt
import hashlib
import html
import re

import pandas as pd
import pyarrow as pa

from undercurrent.ingest.fips import fips_to_iso

RAW_COLUMNS = (
    "record_id",
    "date_int",
    "domain",
    "url",
    "title",
    "locations",
    "persons",
    "orgs",
    "themes",
    "tone",
    "translation_info",
)

LOCATION = pa.struct(
    [
        ("name", pa.string()),
        ("country", pa.string()),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("type", pa.int32()),
    ]
)
# The column order and types of docs/WORKPLAN.md 3.1. `day` is not stored in the files: it is
# the folder name (articles/day=YYYY-MM-DD/), which readers turn back into a column.
ARTICLES_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("url", pa.string()),
        ("domain", pa.string()),
        ("published_at", pa.timestamp("us", tz="UTC")),
        ("title", pa.string()),
        ("locations", pa.list_(LOCATION)),
        ("persons", pa.list_(pa.string())),
        ("orgs", pa.list_(pa.string())),
        ("themes", pa.list_(pa.string())),
        ("tone", pa.float64()),
        ("dup_count", pa.int32()),
        ("sampled", pa.bool_()),
        ("translated", pa.bool_()),
        ("lang", pa.string()),
    ]
)
TOTALS_SCHEMA = pa.schema(
    [
        ("feed", pa.string()),  # "english" or "translated"
        ("country", pa.string()),  # ISO alpha-2 of the article's first location; null if none
        ("records", pa.int64()),  # GKG rows, before any filtering
        ("with_title", pa.int64()),  # of those, rows with a <PAGE_TITLE>
    ]
)

_SPACE = re.compile(r"\s+")
_SRC_LANG = re.compile(r"srclc:([A-Za-z]+)")


def article_id(url: str) -> str:
    """First 16 hex characters of sha1(url): stable across reruns and machines."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def clean_title(raw: object) -> str:
    """HTML-unescaped, whitespace-collapsed headline ("" if missing)."""
    if not isinstance(raw, str):
        return ""
    return _SPACE.sub(" ", html.unescape(raw)).strip()


def split_list(raw: object) -> list[str]:
    """'a;b;;a' -> ['a', 'b']: order kept, empties and repeats dropped, V2 ',offset' removed."""
    if not isinstance(raw, str) or not raw:
        return []
    seen: dict[str, None] = {}
    for part in raw.split(";"):
        head, _, tail = part.rpartition(",")
        value = (head if head and tail.isdigit() else part).strip()
        if value:
            seen.setdefault(value, None)
    return list(seen)


def parse_locations(raw: object) -> list[dict]:
    """GKG locations -> [{name, country (ISO alpha-2), lat, lon, type}], in mention order.

    Accepts V1 (7 fields: type#name#FIPS#adm1#lat#lon#featureid) and V2 enhanced (9 fields,
    with adm2 after adm1 and a character offset at the end). Places without coordinates are
    dropped; repeats of the same place keep their first mention.
    """
    if not isinstance(raw, str) or not raw:
        return []
    out: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for entry in raw.split(";"):
        f = entry.split("#")
        if len(f) >= 9:  # V2: type#name#cc#adm1#adm2#lat#lon#featureid#offset
            kind, name, cc, lat, lon = f[0], f[1], f[2], f[5], f[6]
        elif len(f) >= 7:  # V1: type#name#cc#adm1#lat#lon#featureid
            kind, name, cc, lat, lon = f[0], f[1], f[2], f[4], f[5]
        else:
            continue
        try:
            place_type, latitude, longitude = int(kind), float(lat), float(lon)
        except ValueError:
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        key = (name, place_type)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "name": name,
                "country": fips_to_iso(cc),
                "lat": latitude,
                "lon": longitude,
                "type": place_type,
            }
        )
    return out


def parse_tone(raw: object) -> float | None:
    """First value of V2Tone: the average tone (negative = negative coverage)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return float(raw.split(",")[0])
    except ValueError:
        return None


def source_language(raw: object) -> str | None:
    """'srclc:fra;eng:...' -> 'fra'. None for English-feed rows."""
    if not isinstance(raw, str):
        return None
    m = _SRC_LANG.search(raw)
    return m.group(1).lower() if m else None


def published_at(date_int: pd.Series) -> pd.Series:
    """GKG DATE (YYYYMMDDHHMMSS integer) -> UTC timestamps."""
    return pd.to_datetime(date_int.astype("int64").astype(str), format="%Y%m%d%H%M%S", utc=True)


def _feed(record_ids: pd.Series, langs: pd.Series) -> pd.Series:
    return record_ids.str.contains("-T", regex=False).fillna(False) | langs.notna()


def daily_totals(raw: pd.DataFrame) -> pa.Table:
    """Counts per feed and first-location country, before any filtering or dedupe."""
    if raw.empty:
        return TOTALS_SCHEMA.empty_table()
    langs = raw["translation_info"].map(source_language)
    frame = pd.DataFrame(
        {
            "feed": _feed(raw["record_id"], langs).map({True: "translated", False: "english"}),
            "country": raw["locations"].map(
                lambda s: (
                    fips_to_iso(s.split("#")[2])
                    if isinstance(s, str) and s.count("#") >= 2
                    else None
                )
            ),
            "has_title": raw["title"].map(lambda t: bool(clean_title(t))),
        }
    )
    grouped = (
        frame.groupby(["feed", "country"], dropna=False)
        .agg(records=("has_title", "size"), with_title=("has_title", "sum"))
        .reset_index()
        .sort_values(["feed", "country"], na_position="last")
    )
    grouped["country"] = grouped["country"].astype(object).where(grouped["country"].notna(), None)
    return pa.Table.from_pandas(grouped, schema=TOTALS_SCHEMA, preserve_index=False)


def build_articles(raw: pd.DataFrame, *, sampled: bool = False) -> pa.Table:
    """One day's raw rows -> deduped articles in the WORKPLAN 3.1 schema, oldest first."""
    if raw.empty:
        return ARTICLES_SCHEMA.empty_table()
    df = raw.loc[:, list(RAW_COLUMNS)].copy()
    df["title"] = df["title"].map(clean_title)
    df = df[(df["title"] != "") & df["url"].notna() & (df["url"] != "")]
    if df.empty:
        return ARTICLES_SCHEMA.empty_table()
    df["published_at"] = published_at(df["date_int"])
    df = df.sort_values(["published_at", "record_id"], kind="stable")

    # 1. The same URL (it can appear in both feeds, or twice in one): keep its first row.
    df = df.drop_duplicates("url", keep="first")
    # 2. Syndicated copies: the same normalised headline on other sites folds into the first.
    key = df["title"].str.lower()
    dup_count = key.map(key.value_counts())
    keep = ~key.duplicated(keep="first")
    df, dup_count = df[keep], dup_count[keep]

    lang = df["translation_info"].map(source_language)
    return pa.Table.from_pydict(
        {
            "id": [article_id(u) for u in df["url"]],
            "url": df["url"].tolist(),
            # pandas 3 strings use NaN for missing; `.where(..., None)` keeps it NaN
            "domain": [d if isinstance(d, str) and d else None for d in df["domain"]],
            "published_at": df["published_at"].dt.to_pydatetime().tolist(),
            "title": df["title"].tolist(),
            "locations": df["locations"].map(parse_locations).tolist(),
            "persons": df["persons"].map(split_list).tolist(),
            "orgs": df["orgs"].map(split_list).tolist(),
            "themes": df["themes"].map(split_list).tolist(),
            "tone": df["tone"].map(parse_tone).tolist(),
            "dup_count": dup_count.astype("int32").tolist(),
            "sampled": [sampled] * len(df),
            "translated": _feed(df["record_id"], lang).tolist(),
            "lang": [x if isinstance(x, str) else None for x in lang],
        },
        schema=ARTICLES_SCHEMA,
    )


def split_by_day(raw: pd.DataFrame) -> dict[dt.date, pd.DataFrame]:
    """Raw rows grouped by the UTC day of their GKG DATE."""
    if raw.empty:
        return {}
    days = pd.to_datetime(
        raw["date_int"].astype("int64").astype(str).str[:8], format="%Y%m%d"
    ).dt.date
    return {day: part for day, part in raw.groupby(days, sort=True)}
