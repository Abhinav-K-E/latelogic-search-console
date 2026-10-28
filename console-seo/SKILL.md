---
name: console-seo
description: "Pull Google Search Console data for the user's own sites with their default service account: list the properties it can access, ask which one to open, then report clicks, impressions, CTR, average position, top queries and pages, device split, trends, period-over-period changes, striking-distance keywords, sitemaps and URL index status. Use this whenever the user types /console-seo, or asks about Search Console / GSC, search traffic, rankings, impressions, which keywords or pages a site ranks for, why organic traffic rose or dropped, quick-win keywords, whether a page is indexed, or sitemap status — for seatinfo.com, flightpoints.com, flightflyer.com, roompoints.com or any other site they own — even if they don't say 'Search Console'."
argument-hint: "[site] [what you want to see]"
---

# console-seo

Search Console reports for the user's properties, authenticated with a service account key that is already set up. You don't need to ask the user for credentials: the helper script finds the key on its own.

All data comes from one helper script, run with `uv` (it installs its own Google client libraries on first run):

```bash
uv run --script ~/.claude/skills/console-seo/scripts/gsc.py <command> [args]
```

The key is read from `--key`, then `$GSC_CREDENTIALS_PATH`, then the default `~/Library/Application Support/mcp-gsc/service_account.json` (the same file the `gsc` MCP server uses, so updating it there updates both). The script uses a read-only scope.

## Workflow

### 1. Pick the property

If the user already named a site (in the `/console-seo` arguments or their message), pass it straight to the report command: the script accepts the exact property, a bare domain (`seatinfo.com`) or a unique fragment (`seatinfo`), and errors with the list of properties if nothing or several match.

Otherwise list what the account can see:

```bash
uv run --script ~/.claude/skills/console-seo/scripts/gsc.py properties
```

Show the table, then ask with AskUserQuestion which property to open, one option per property (AskUserQuestion takes at most 4 options; with more properties, offer the 3 the user seems most likely to want and let "Other" cover the rest). In the same AskUserQuestion call, if the user hasn't said what they want, ask a second question for the report, with these options:

- **Performance overview (Recommended)**: totals, devices, trend, top queries and pages
- **Compare with previous period**: what grew, what dropped, new and lost queries or pages
- **Quick-win keywords**: queries at positions 4-20 with real impressions
- **Indexing and sitemaps**: sitemap status, plus URL inspection for pages they name

Asking both at once saves the user a round trip. Skip any question whose answer is already clear.

### 2. Run the report

| Want | Command |
|---|---|
| First look at a site | `overview SITE [--days 28]` |
| Custom breakdown | `query SITE --dims query,page [--filter "query contains seat map"] [--limit 100] [--order impressions]` |
| What changed | `compare SITE [--days 28] [--dim query\|page\|country\|device]` |
| Quick wins | `striking SITE [--min-impr 50] [--min-pos 4] [--max-pos 20]` |
| Sitemaps | `sitemaps SITE` |
| Is a page indexed | `inspect SITE URL [URL ...]` |

Every command that covers a date range takes `--days N` or `--start YYYY-MM-DD --end YYYY-MM-DD`, plus `--type web|image|video|news|discover|googleNews` and `--final` (finalized data only). Filters use `DIMENSION OPERATOR VALUE` with operators `equals`, `notEquals`, `contains`, `notContains`, `includingRegex`, `excludingRegex`. Repeat `--filter` to AND several together. Countries are ISO alpha-3 (`usa`, `ind`) and devices are `DESKTOP`, `MOBILE` or `TABLET`.

Google returns rows ordered by clicks, and `--order` only re-sorts the rows that were fetched. When ranking by impressions, CTR or position, pass `--limit 25000`. Otherwise high-impression, zero-click rows (often the most interesting ones) never make it into the fetch. Pipe through `head` to keep the output short.

Run more than one command when the question needs it. "Why did traffic drop?" is `compare` by query and then by page. "How's the site doing?" is `overview` plus `sitemaps`.

### 3. Report back

Lead with the answer in a sentence or two: headline numbers and direction. Then the tables that matter, trimmed to what supports the point. Then the two to four things worth acting on, each backed by the numbers. Typical patterns:

- **High impressions, low CTR**: pages or queries ranking on page 1 that people skip, which usually means the title or meta description needs work.
- **Positions 4-20 with volume**: content that already ranks and can move up with on-page work and internal links.
- **Sharp gainers or losers**: name the specific queries or pages, not just the totals.
- **Branded vs non-branded**: when the site's own name dominates the clicks, say how much the non-branded traffic is.
- **Sitemap errors, or no sitemap at all.** An empty `sitemaps` list only means nothing was submitted on the property. Google may still read one from robots.txt. Before calling it missing, run `inspect` on two or three key URLs: a "Submitted and indexed" coverage state, or the "Sitemaps Google found these URLs in" section, shows that Google has a sitemap.
- **Mobile vs desktop gaps** in position or CTR.

Keep the claims to what the data shows. Search Console tells you what happened, not why, so present causes as likely explanations to check.

Things to keep in mind when reading the numbers:

- Dates are Pacific Time. Data lags 2-3 days, and the most recent day is usually partial unless `--final` is used, so don't read a low last day as a drop.
- Anonymized queries are left out, so query rows add up to less than the site total.
- A domain property (`sc-domain:`) covers every subdomain and protocol. A URL-prefix property covers only that prefix, so their totals differ.
- Average position is impression-weighted. A page can "improve" just by losing low-ranking impressions.

After the report, offer a sensible next step, such as drilling into a page's queries, comparing a longer range, or rewriting a page that has a CTR problem (the `seo-content-master` skill does rewrites).

## Changes and troubleshooting

- **403 / permission denied**: the service account isn't a user on that property. The user has to add its email (shown by `properties`) in Search Console under Settings → Users and permissions.
- **Key not found**: the default file is missing. Ask the user for the key's path and pass `--key`, or copy the key to the default location.
- **Using a different key**: pass `--key /path/to/other.json` for one run, or replace the default file to change it permanently.
- **Writes** such as submitting a sitemap aren't in this read-only script. Use the `gsc` MCP server's `submit_sitemap` tool if it's connected, and only after the user confirms, because it changes their Search Console.
