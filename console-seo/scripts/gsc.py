#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["google-auth>=2.20", "google-api-python-client>=2.100"]
# ///
"""Google Search Console reports for the console-seo skill.

Authenticates with a service account key (read-only scope) and prints markdown.
Key lookup order: --key, $GSC_CREDENTIALS_PATH, then DEFAULT_KEY.

Commands:
  properties                       list properties the key can access
  overview  SITE                   totals, devices, trend, top queries and pages
  query     SITE --dims query,page custom breakdown with optional --filter
  compare   SITE                   this period vs the previous one of equal length
  striking  SITE                   query/page pairs ranking just outside the top results
  sitemaps  SITE                   submitted sitemaps and their status
  inspect   SITE URL [URL ...]     index status of specific URLs (URL Inspection API)

SITE may be the exact property (sc-domain:example.com, https://www.example.com/),
a bare domain (example.com) or a unique fragment (example).
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import httplib2
from google.oauth2 import service_account
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

DEFAULT_KEY = Path.home() / "Library/Application Support/mcp-gsc/service_account.json"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
PT = ZoneInfo("America/Los_Angeles")  # Search Console reports in Pacific Time
PERMISSIONS = {
    "siteOwner": "Owner",
    "siteFullUser": "Full",
    "siteRestrictedUser": "Restricted",
    "siteUnverifiedUser": "Unverified",
}
TIMEOUT = 180  # seconds; URL Inspection can be slower than the client's 60 s default
RETRIES = 2  # retried on timeouts, 429 and 5xx
OPERATORS = ["equals", "notEquals", "contains", "notContains", "includingRegex", "excludingRegex"]
M = ["Clicks", "Impressions", "CTR", "Position"]


def die(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def key_path(arg: str | None) -> Path:
    p = Path(arg or os.environ.get("GSC_CREDENTIALS_PATH") or DEFAULT_KEY).expanduser()
    if not p.is_file():
        die(f"service account key not found at {p}. Pass --key /path/to/key.json or set GSC_CREDENTIALS_PATH.")
    return p


def connect(key: Path):
    creds = service_account.Credentials.from_service_account_file(str(key), scopes=SCOPES)
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=TIMEOUT))
    return build("searchconsole", "v1", http=http, cache_discovery=False), creds.service_account_email


# ---------- formatting ----------

def cell(v) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return "_No rows._"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def metrics(r: dict) -> list[str]:
    return [f"{r['clicks']:,.0f}", f"{r['impressions']:,.0f}", f"{r['ctr'] * 100:.2f}%", f"{r['position']:.1f}"]


def combine(rows: list[dict]) -> dict:
    """Sum clicks/impressions; CTR from the sums; position weighted by impressions."""
    clicks = sum(r["clicks"] for r in rows)
    impr = sum(r["impressions"] for r in rows)
    pos = sum(r["position"] * r["impressions"] for r in rows) / impr if impr else 0.0
    return {"clicks": clicks, "impressions": impr, "ctr": clicks / impr if impr else 0.0, "position": pos}


def pct(cur: float, prev: float) -> str:
    return f"{(cur - prev) / prev * 100:+.1f}%" if prev else "n/a"


# ---------- API helpers ----------

def host(site_url: str) -> str:
    h = site_url.removeprefix("sc-domain:").split("://", 1)[-1]
    return h.split("/", 1)[0].removeprefix("www.").lower()


def list_sites(svc) -> list[dict]:
    return sorted(svc.sites().list().execute(num_retries=RETRIES).get("siteEntry", []), key=lambda s: s["siteUrl"])


def resolve_site(svc, term: str) -> str:
    sites = [s["siteUrl"] for s in list_sites(svc)]
    if term in sites:
        return term
    want = host(term)
    matches = [u for u in sites if host(u) == want] or [u for u in sites if want in host(u)]
    domain = [u for u in matches if u.startswith("sc-domain:")]
    if len(matches) > 1 and len(domain) == 1:
        # A domain property covers every URL-prefix property on the same host.
        others = ", ".join(u for u in matches if u != domain[0])
        print(f"_Using {domain[0]} (also available: {others})._\n")
        return domain[0]
    if len(matches) == 1:
        return matches[0]
    available = ", ".join(sites) or "none"
    if matches:
        die(f"'{term}' matches several properties: {', '.join(matches)}. Pass one exactly.")
    die(f"no property matches '{term}'. Available: {available}")


def date_range(a) -> tuple[dt.date, dt.date]:
    end = dt.date.fromisoformat(a.end) if a.end else dt.datetime.now(PT).date() - dt.timedelta(days=1)
    start = dt.date.fromisoformat(a.start) if a.start else end - dt.timedelta(days=a.days - 1)
    if start > end:
        die(f"start date {start} is after end date {end}")
    return start, end


def sa(svc, site, a, start, end, dims, limit=1000, filters=None, start_row=0) -> list[dict]:
    body = {
        "startDate": str(start),
        "endDate": str(end),
        "dimensions": dims,
        "rowLimit": limit,
        "startRow": start_row,
        "type": a.type,
        "dataState": "final" if a.final else "all",
    }
    if filters:
        body["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]
    return svc.searchanalytics().query(siteUrl=site, body=body).execute(num_retries=RETRIES).get("rows", [])


def parse_filter(text: str) -> dict:
    parts = text.split(maxsplit=2)
    if len(parts) != 3 or parts[1] not in OPERATORS:
        die(f"bad --filter '{text}'. Use 'DIMENSION OPERATOR VALUE', operators: {', '.join(OPERATORS)}")
    return {"dimension": parts[0], "operator": parts[1], "expression": parts[2]}


# ---------- commands ----------

def cmd_properties(svc, email, key, a):
    sites = list_sites(svc)
    print(f"Service account: `{email}`  \nKey: `{key}`\n")
    rows = [[s["siteUrl"], "Domain" if s["siteUrl"].startswith("sc-domain:") else "URL prefix",
             PERMISSIONS.get(s["permissionLevel"], s["permissionLevel"])] for s in sites]
    print(table(["Property", "Type", "Permission"], rows))
    if not sites:
        print(f"\nAdd `{email}` as a user on each property in Search Console "
              "(Settings → Users and permissions → Add user).")


def cmd_overview(svc, site, a):
    start, end = date_range(a)
    days = (end - start).days + 1
    print(f"## {site} · {start} to {end} ({days} days, {a.type})\n")
    total = sa(svc, site, a, start, end, [], 1)
    print(table(M, [metrics(total[0])] if total else []))

    print("\n### Devices\n")
    print(table(["Device"] + M, [[r["keys"][0]] + metrics(r) for r in sa(svc, site, a, start, end, ["device"], 10)]))

    daily = sa(svc, site, a, start, end, ["date"], 500)
    if days <= 14:
        print("\n### Daily trend\n")
        print(table(["Date"] + M, [[r["keys"][0]] + metrics(r) for r in daily]))
    else:
        print("\n### Weekly trend (7-day buckets from the start date)\n")
        buckets: dict[int, list[dict]] = {}
        for r in daily:
            buckets.setdefault((dt.date.fromisoformat(r["keys"][0]) - start).days // 7, []).append(r)
        rows = []
        for i in sorted(buckets):
            b_start = start + dt.timedelta(days=7 * i)
            b_end = min(b_start + dt.timedelta(days=6), end)
            rows.append([f"{b_start} to {b_end}", f"{len(buckets[i])}/{(b_end - b_start).days + 1}"]
                        + metrics(combine(buckets[i])))
        print(table(["Week", "Days with data"] + M, rows))
    if daily and daily[-1]["keys"][0] == str(end) and not a.final:
        print(f"\n_{end} may still be filling in (fresh data lags 2-3 days)._")

    if a.type not in ("discover", "googleNews"):
        print("\n### Top queries\n")
        print(table(["Query"] + M, [[r["keys"][0]] + metrics(r) for r in sa(svc, site, a, start, end, ["query"], 10)]))
    print("\n### Top pages\n")
    print(table(["Page"] + M, [[r["keys"][0]] + metrics(r) for r in sa(svc, site, a, start, end, ["page"], 10)]))


def cmd_query(svc, site, a):
    start, end = date_range(a)
    dims = [d for d in a.dims.split(",") if d]
    filters = [parse_filter(f) for f in a.filter or []]
    rows = sa(svc, site, a, start, end, dims, a.limit, filters, a.start_row)
    key = a.order.lower()
    rows.sort(key=lambda r: r[key], reverse=not a.asc)
    print(f"## {site} · {start} to {end} · by {', '.join(dims) or 'total'}\n")
    if filters:
        print("Filters: " + "; ".join(f"{f['dimension']} {f['operator']} '{f['expression']}'" for f in filters) + "\n")
    print(table([d.title() for d in dims] + M, [r.get("keys", []) + metrics(r) for r in rows]))
    if rows and dims:
        print(f"\n{len(rows)} rows · totals of these rows: " + " · ".join(f"{h} {v}" for h, v in zip(M[:2], metrics(combine(rows)))))
        if len(rows) == a.limit:
            print(f"_Row limit hit; more with --start-row {a.start_row + a.limit}._")


def cmd_compare(svc, site, a):
    cur_s, cur_e = date_range(a)
    length = (cur_e - cur_s).days + 1
    prev_e = cur_s - dt.timedelta(days=1)
    prev_s = prev_e - dt.timedelta(days=length - 1)
    print(f"## {site} · {cur_s} to {cur_e} vs {prev_s} to {prev_e}\n")

    zero = {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0}
    tc = (sa(svc, site, a, cur_s, cur_e, [], 1) or [zero])[0]
    tp = (sa(svc, site, a, prev_s, prev_e, [], 1) or [zero])[0]
    pos_d = tc["position"] - tp["position"]
    print(table(["Metric", "Previous", "Current", "Change"], [
        ["Clicks", f"{tp['clicks']:,.0f}", f"{tc['clicks']:,.0f}", f"{tc['clicks'] - tp['clicks']:+,.0f} ({pct(tc['clicks'], tp['clicks'])})"],
        ["Impressions", f"{tp['impressions']:,.0f}", f"{tc['impressions']:,.0f}",
         f"{tc['impressions'] - tp['impressions']:+,.0f} ({pct(tc['impressions'], tp['impressions'])})"],
        ["CTR", f"{tp['ctr'] * 100:.2f}%", f"{tc['ctr'] * 100:.2f}%", f"{(tc['ctr'] - tp['ctr']) * 100:+.2f} pp"],
        ["Position", f"{tp['position']:.1f}", f"{tc['position']:.1f}",
         f"{pos_d:+.1f}" + (" (better)" if pos_d < 0 else " (worse)" if pos_d > 0 else "")],
    ]))

    cur = {r["keys"][0]: r for r in sa(svc, site, a, cur_s, cur_e, [a.dim], 5000)}
    prev = {r["keys"][0]: r for r in sa(svc, site, a, prev_s, prev_e, [a.dim], 5000)}

    def moved(k):
        c, p = cur[k], prev[k]
        return [k, f"{p['clicks']:,.0f} → {c['clicks']:,.0f} ({c['clicks'] - p['clicks']:+,.0f})",
                f"{p['impressions']:,.0f} → {c['impressions']:,.0f}", f"{p['position']:.1f} → {c['position']:.1f}"]

    both = sorted((k for k in cur if k in prev), key=lambda k: cur[k]["clicks"] - prev[k]["clicks"])
    head = [a.dim.title(), "Clicks", "Impressions", "Position"]
    print(f"\n### Top gainers ({a.dim})\n")
    print(table(head, [moved(k) for k in reversed(both) if cur[k]["clicks"] > prev[k]["clicks"]][:a.limit]))
    print(f"\n### Top losers ({a.dim})\n")
    print(table(head, [moved(k) for k in both if cur[k]["clicks"] < prev[k]["clicks"]][:a.limit]))
    print(f"\n### New this period ({a.dim})\n")
    new = sorted((k for k in cur if k not in prev), key=lambda k: -cur[k]["clicks"])[:a.limit]
    print(table([a.dim.title()] + M, [[k] + metrics(cur[k]) for k in new]))
    print(f"\n### Lost since last period ({a.dim})\n")
    lost = sorted((k for k in prev if k not in cur), key=lambda k: -prev[k]["clicks"])[:a.limit]
    print(table([a.dim.title()] + M, [[k] + metrics(prev[k]) for k in lost]))


def cmd_striking(svc, site, a):
    start, end = date_range(a)
    rows = [r for r in sa(svc, site, a, start, end, ["query", "page"], 25000)
            if a.min_pos <= r["position"] <= a.max_pos and r["impressions"] >= a.min_impr]
    rows.sort(key=lambda r: -r["impressions"])
    print(f"## {site} · striking distance (position {a.min_pos:g}-{a.max_pos:g}, ≥{a.min_impr} impressions) · "
          f"{start} to {end}\n")
    print(table(["Query", "Page", "Impressions", "Clicks", "CTR", "Position"],
                [[r["keys"][0], r["keys"][1], f"{r['impressions']:,.0f}", f"{r['clicks']:,.0f}",
                  f"{r['ctr'] * 100:.2f}%", f"{r['position']:.1f}"] for r in rows[:a.limit]]))
    if len(rows) > a.limit:
        print(f"\n_{len(rows) - a.limit} more pairs; raise --limit to see them._")


def cmd_sitemaps(svc, site, a):
    maps = svc.sitemaps().list(siteUrl=site).execute(num_retries=RETRIES).get("sitemap", [])
    print(f"## {site} · sitemaps\n")
    if not maps:
        print("No sitemaps are submitted for this property. Google may still find one through robots.txt; "
              "`inspect` on a few URLs shows which sitemaps it knows them from.")
        return
    rows = [[m["path"], m.get("type", ""), "yes" if m.get("isSitemapsIndex") else "no",
             m.get("lastSubmitted", "")[:10], m.get("lastDownloaded", "")[:10],
             "yes" if m.get("isPending") else "no", m.get("errors", 0), m.get("warnings", 0),
             sum(int(c.get("submitted", 0)) for c in m.get("contents", []))] for m in maps]
    print(table(["Path", "Type", "Index?", "Submitted", "Downloaded", "Pending", "Errors", "Warnings", "URLs"], rows))


def cmd_inspect(svc, site, a):
    rows, mismatches, sitemaps = [], [], {}
    for url in dict.fromkeys(a.urls):
        try:
            res = svc.urlInspection().index().inspect(
                body={"inspectionUrl": url, "siteUrl": site, "languageCode": "en-US"}).execute(num_retries=RETRIES)
        except HttpError as e:
            rows.append([url, "error", e.reason or e.status_code, "", "", "", ""])
            continue
        except OSError as e:  # timeouts and dropped connections: keep going with the other URLs
            rows.append([url, "error", type(e).__name__, "", "", "", ""])
            continue
        r = res.get("inspectionResult", {}).get("indexStatusResult", {})
        google, declared = r.get("googleCanonical"), r.get("userCanonical") or url
        ok = "-" if not google else "yes" if google == declared else "no"
        if ok == "no":
            mismatches.append(f"- {url}: Google chose {google}, declared {declared}")
        for sm in r.get("sitemap", []):
            sitemaps.setdefault(sm, []).append(url)
        rows.append([url, r.get("verdict", ""), r.get("coverageState", ""), r.get("indexingState", ""),
                     r.get("robotsTxtState", ""), (r.get("lastCrawlTime") or "")[:10], ok])
    print(f"## {site} · URL inspection\n")
    print(table(["URL", "Verdict", "Coverage", "Indexing", "robots.txt", "Last crawl", "Canonical OK"], rows))
    if mismatches:
        print("\n### Canonical mismatches\n\n" + "\n".join(mismatches))
    if sitemaps:
        print("\n### Sitemaps Google found these URLs in\n")
        print("\n".join(f"- {sm} ({len(urls)} of the URLs)" for sm, urls in sitemaps.items()))


# ---------- CLI ----------

def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--key", help="service account JSON (default: $GSC_CREDENTIALS_PATH or the mcp-gsc key)")
    common.add_argument("--type", default="web", choices=["web", "image", "video", "news", "discover", "googleNews"])
    common.add_argument("--final", action="store_true", help="finalized data only (default includes fresh data)")
    ranged = argparse.ArgumentParser(add_help=False, parents=[common])
    ranged.add_argument("site")
    ranged.add_argument("--days", type=int, default=28)
    ranged.add_argument("--start", help="YYYY-MM-DD (Pacific Time)")
    ranged.add_argument("--end", help="YYYY-MM-DD, default yesterday")

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("properties", parents=[common])
    sub.add_parser("overview", parents=[ranged])
    q = sub.add_parser("query", parents=[ranged])
    q.add_argument("--dims", default="query", help="comma list of query,page,country,device,date,searchAppearance")
    q.add_argument("--filter", action="append", help="'DIMENSION OPERATOR VALUE', repeatable (ANDed)")
    q.add_argument("--limit", type=int, default=100)
    q.add_argument("--start-row", type=int, default=0)
    q.add_argument("--order", default="clicks", choices=["clicks", "impressions", "ctr", "position"])
    q.add_argument("--asc", action="store_true")
    c = sub.add_parser("compare", parents=[ranged])
    c.add_argument("--dim", default="query", choices=["query", "page", "country", "device", "searchAppearance"])
    c.add_argument("--limit", type=int, default=15)
    s = sub.add_parser("striking", parents=[ranged])
    s.add_argument("--min-impr", type=int, default=50)
    s.add_argument("--min-pos", type=float, default=4.0)
    s.add_argument("--max-pos", type=float, default=20.0)
    s.add_argument("--limit", type=int, default=50)
    sm = sub.add_parser("sitemaps", parents=[common])
    sm.add_argument("site")
    i = sub.add_parser("inspect", parents=[common])
    i.add_argument("site")
    i.add_argument("urls", nargs="+")
    a = p.parse_args()

    key = key_path(a.key)
    svc, email = connect(key)
    try:
        if a.cmd == "properties":
            cmd_properties(svc, email, key, a)
            return
        site = resolve_site(svc, a.site)
        {"overview": cmd_overview, "query": cmd_query, "compare": cmd_compare, "striking": cmd_striking,
         "sitemaps": cmd_sitemaps, "inspect": cmd_inspect}[a.cmd](svc, site, a)
    except HttpError as e:
        if e.status_code == 403:
            die(f"permission denied ({e.reason}). Add {email} as a user on the property in Search Console "
                "(Settings → Users and permissions).")
        die(f"Search Console API returned {e.status_code}: {e.reason}")


if __name__ == "__main__":
    main()
