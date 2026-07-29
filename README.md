# mc-funding-tracker

Tracks fundraising activity for startups founded by Dartmouth alumni.

Two ways funding info gets in:

1. **Automated research** — give it a founder name + company name and it searches the
   web (via the Anthropic API) for funding announcements and news coverage.
2. **Report a funding update** — describe what you heard in plain English (e.g. "closed
   a $2M seed round in July 2025") and it's parsed into a structured, confirmed round.

Plus a local web dashboard to browse companies and rounds.

## Installation

```bash
cd ~/Projects/mc-funding-tracker
pip install -e .
```

## Configuration

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

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
one or more founders with class year). Click **Run Research** on a company's page to
search the web for funding rounds in the background (this can take up to a couple of
minutes; the page shows a "Researching…" status until it's done). Anything research
finds shows up flagged "unconfirmed" until you review and confirm it. Use **Report a
Funding Update** any time to log something you heard as a confirmed round.

You can also trigger a research pass from the command line:

```bash
mc-funding-tracker research <company-id>
```

## Data

Config and the SQLite database live in `~/.config/mc-funding-tracker/`. Logs go to
`~/.config/mc-funding-tracker/tracker.log`.
