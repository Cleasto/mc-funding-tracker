# mc-funding-tracker

Tracks fundraising activity for startups founded by Dartmouth alumni.

Three ways funding info gets in:

1. **Automated research** — give it a founder name + company name and it checks SEC Form D
   filings (free, no API key) and does a web/news search (via the Anthropic API) for
   announcements.
2. **Manual notes** — log something you heard before it's anywhere official.
3. A local web dashboard to browse companies, rounds, and notes.

## Installation

```bash
cd ~/Projects/mc-funding-tracker
pip install -e .
```

## Configuration

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
mc-funding-tracker configure --contact-email "you@example.com"
```

The contact email is sent as part of the User-Agent header on SEC EDGAR requests, per
[SEC's fair access policy](https://www.sec.gov/os/webmaster-faq#developers).

View current configuration:

```bash
mc-funding-tracker status
```

## Usage

Start the dashboard:

```bash
mc-funding-tracker serve
```

Then open http://127.0.0.1:5430 and add a company (name, website, Dartmouth IP yes/no,
one or more founders with class year). Click **Run Research** on a company's page to pull
in SEC Form D filings and web-search-sourced funding rounds; anything from research shows
up flagged "unconfirmed" until you review and confirm it. Add freeform notes any time from
the company page.

You can also trigger a research pass from the command line:

```bash
mc-funding-tracker research <company-id>
```

## Data

Config and the SQLite database live in `~/.config/mc-funding-tracker/`. Logs go to
`~/.config/mc-funding-tracker/tracker.log`.
