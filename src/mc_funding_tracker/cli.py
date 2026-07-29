"""Command-line interface for mc-funding-tracker."""
from __future__ import annotations

import logging
import sys

import click

from . import __version__
from .config import CONFIG_DIR, CONFIG_FILE, LOG_FILE, load_config, save_config


def _configure_logging() -> None:
    """Send library log output (research progress, errors) to LOG_FILE.

    Configured up front rather than added later — a logging gap was the single
    biggest thing that slowed down debugging a similar pipeline previously.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(LOG_FILE)],
    )


@click.group()
@click.version_option(version=__version__)
def main():
    """mc-funding-tracker - track fundraising by Dartmouth-alumni-founded startups."""
    pass


@main.command()
@click.option("--port", default=5430, show_default=True, help="Port to listen on")
def serve(port: int):
    """Start the dashboard and open it in a browser."""
    from . import server

    _configure_logging()
    config = load_config()
    server.PORT = port
    server.open_dashboard(config)
    click.echo(f"mc-funding-tracker running at http://127.0.0.1:{port} (Ctrl+C to stop)")
    try:
        server._thread.join()
    except KeyboardInterrupt:
        pass


@main.command()
@click.argument("company_id", type=int)
def research(company_id: int):
    """Run the research pipeline for a company from the command line."""
    from .research import run_research

    _configure_logging()
    config = load_config()
    try:
        summary = run_research(company_id, config)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo(
        f"Web research: found {summary['web_found']}, inserted {summary['web_inserted']}"
    )
    if summary["errors"]:
        for err in summary["errors"]:
            click.echo(f"Error: {err}", err=True)
        sys.exit(1)


@main.command()
@click.option("--api-key", help="Anthropic API key")
@click.option("--contact-email", help="Contact email sent in the SEC EDGAR User-Agent header")
@click.option("--claude-model", help="Claude model to use for web research")
def configure(api_key, contact_email, claude_model):
    """Configure mc-funding-tracker settings."""
    config = load_config()

    if api_key:
        config["anthropic_api_key"] = api_key
    if contact_email:
        config["sec_contact_email"] = contact_email
    if claude_model:
        config["claude_model"] = claude_model

    save_config(config)
    click.echo(f"Configuration saved to {CONFIG_FILE}")


@main.command()
def status():
    """Show configuration and database counts."""
    from . import db

    config = load_config()
    db.init_db()
    counts = db.get_counts()

    click.echo("mc-funding-tracker Status")
    click.echo("=" * 40)
    click.echo(f"Config file: {CONFIG_FILE}")
    click.echo(f"Log file: {LOG_FILE}")
    click.echo(f"API key configured: {'Yes' if config.get('anthropic_api_key') else 'No'}")
    click.echo(f"SEC contact email: {config.get('sec_contact_email') or 'Not set'}")
    click.echo(f"Claude model: {config.get('claude_model')}")
    click.echo(f"Companies tracked: {counts['companies']}")
    click.echo(f"Founders tracked: {counts['founders']}")
    click.echo(f"Funding rounds: {counts['funding_rounds']}")
    click.echo(f"Notes: {counts['notes']}")


if __name__ == "__main__":
    main()
