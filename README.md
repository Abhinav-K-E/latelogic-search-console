# latelogic-search-console

A Claude Code skill, `/console-seo`, for Google Search Console reports on the Latelogic sites. It authenticates with a service account, lists the properties that account can see, asks which one to open, and reports on it: clicks, impressions, CTR, rankings, top queries and pages, period comparisons, quick-win keywords, sitemaps and URL index status.

```
console-seo/
├── SKILL.md         # the workflow Claude follows
└── scripts/gsc.py   # read-only Search Console client (markdown output)
```

## Install

```bash
cp -R console-seo ~/.claude/skills/
```

It shows up as `/console-seo` in new Claude Code sessions. To update an installed copy after pulling changes:

```bash
rsync -a --delete console-seo/ ~/.claude/skills/console-seo/
```

## Credentials

The key is **not** in this repo, and `.gitignore` blocks JSON files so a key can't be committed by accident.

1. In Google Cloud, enable the **Google Search Console API** and create a service account key (JSON).
2. Save it as `~/Library/Application Support/mcp-gsc/service_account.json`. To keep it somewhere else, set `GSC_CREDENTIALS_PATH` or pass `--key`.
3. In Search Console, add the service account's email as a user on each property (Settings → Users and permissions).

Requires [`uv`](https://docs.astral.sh/uv/). The script declares its own dependencies and `uv` installs them on first run.

## Usage

In Claude Code:

```
/console-seo
/console-seo seatinfo why did traffic jump
/console-seo flightpoints impressions this month
```

The script also runs on its own:

```bash
uv run --script console-seo/scripts/gsc.py properties
uv run --script console-seo/scripts/gsc.py overview seatinfo --days 28
uv run --script console-seo/scripts/gsc.py compare flightpoints --dim page
uv run --script console-seo/scripts/gsc.py striking roompoints
uv run --script console-seo/scripts/gsc.py query flightpoints --dims page --order impressions --limit 25000
uv run --script console-seo/scripts/gsc.py inspect seatinfo https://seatinfo.com/
```

Run `uv run --script console-seo/scripts/gsc.py -h` for every command and flag. The script only reads data (it uses the `webmasters.readonly` scope).
