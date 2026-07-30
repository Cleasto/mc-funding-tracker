"""Flask web dashboard for mc-funding-tracker.

Research (a web-search API call) runs on a background thread tracked via jobs.py
rather than blocking the request — it can take up to ~180s, which was long
enough to hit real client-side timeouts when it ran synchronously. There's no
native GUI here (unlike meetcap's menu bar app), so background threads have no
thread-safety concerns beyond the job tracker's own lock.
"""
from __future__ import annotations

import logging
import os
import threading
import webbrowser
from typing import Any, Dict

from flask import Flask, flash, redirect, render_template, request, url_for
from werkzeug.serving import make_server

from . import db, jobs
from .research import parse_funding_update, run_research

logger = logging.getLogger(__name__)

PORT = 5430

_server = None
_thread = None


def _format_usd(amount) -> str:
    """Render a dollar amount compactly, e.g. 2_000_000 -> '$2M'."""
    if amount is None:
        return "undisclosed"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M".replace(".0M", "M")
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount}"


def _format_class_year(year) -> str:
    """Render a class year compactly, e.g. 2018 -> "'18"."""
    if not year:
        return ""
    return f"'{str(int(year))[-2:]}"


def create_app(config: Dict[str, Any]) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.secret_key = os.urandom(24)
    app.jinja_env.filters["usd"] = _format_usd
    app.jinja_env.filters["classyear"] = _format_class_year
    db.init_db()

    @app.route("/")
    def index():
        companies = db.get_companies()
        grand_total = db.get_grand_total_funding()
        researching_ids = jobs.running_ids()
        return render_template(
            "index.html", companies=companies, grand_total=grand_total, researching_ids=researching_ids
        )

    @app.route("/company/new")
    def new_company():
        return render_template("add_company.html")

    @app.route("/company", methods=["POST"])
    def create_company():
        name = request.form.get("name", "").strip()
        website = request.form.get("website", "").strip()
        dartmouth_ip = request.form.get("dartmouth_ip") == "yes"

        founders = []
        for fname, fyear in zip(
            request.form.getlist("founder_name"),
            request.form.getlist("founder_class_year"),
        ):
            fname = fname.strip()
            if not fname:
                continue
            class_year = int(fyear) if fyear.strip().isdigit() else None
            founders.append({"name": fname, "class_year": class_year})

        if not name:
            flash("Company name is required.", "error")
            return redirect(url_for("new_company"))

        company_id = db.add_company(name, website, dartmouth_ip, founders)
        flash(f"Added {name}.", "success")
        return redirect(url_for("company_detail", company_id=company_id))

    @app.route("/company/<int:company_id>")
    def company_detail(company_id: int):
        company = db.get_company(company_id)
        if company is None:
            return "Company not found", 404

        result = jobs.pop_result(company_id)
        if result is not None:
            if result["status"] == "done":
                summary = result["summary"]
                msg = (
                    f"Research complete: web search found {summary['web_found']} "
                    f"({summary['web_inserted']} new)."
                )
                flash(msg, "error" if summary["errors"] else "success")
                for err in summary["errors"]:
                    flash(err, "error")
            else:
                flash(f"Research failed: {result['error']}", "error")

        return render_template(
            "company.html", company=company, researching=jobs.is_running(company_id)
        )

    @app.route("/company/<int:company_id>/research", methods=["POST"])
    def research_company(company_id: int):
        company = db.get_company(company_id)
        if company is None:
            return "Company not found", 404
        started = jobs.start(company_id, lambda: run_research(company_id, config))
        if not started:
            flash("Research is already running for this company.", "error")
        return redirect(url_for("company_detail", company_id=company_id))

    @app.route("/company/<int:company_id>/report-update", methods=["POST"])
    def report_funding_update(company_id: int):
        text = request.form.get("body", "").strip()
        if not text:
            return redirect(url_for("company_detail", company_id=company_id))
        try:
            parsed = parse_funding_update(text, config)
            db.add_funding_round(
                company_id=company_id,
                round_type=parsed.get("round_type"),
                amount_usd=parsed.get("amount_usd"),
                announced_date=parsed.get("announced_date"),
                investors=parsed.get("investors"),
                source="internal",
                source_url=None,
            )
            flash(f"Recorded: {parsed.get('round_type') or 'funding round'}.", "success")
        except Exception as e:
            logger.exception(f"Failed to parse funding update for company_id={company_id}")
            flash(f"Could not record that update: {e}", "error")
        return redirect(url_for("company_detail", company_id=company_id))

    @app.route("/round/<int:round_id>/confirm", methods=["POST"])
    def confirm_round_route(round_id: int):
        db.confirm_round(round_id)
        return redirect(request.referrer or url_for("index"))

    @app.route("/round/<int:round_id>/reject", methods=["POST"])
    def reject_round_route(round_id: int):
        db.reject_round(round_id)
        flash("Marked as not this company — it won't be re-suggested from that filer.", "success")
        return redirect(request.referrer or url_for("index"))

    @app.route("/round/<int:round_id>/unreject", methods=["POST"])
    def unreject_round_route(round_id: int):
        db.unreject_round(round_id)
        return redirect(request.referrer or url_for("index"))

    @app.route("/company/<int:company_id>/founder", methods=["POST"])
    def add_founder_route(company_id: int):
        name = request.form.get("name", "").strip()
        class_year_raw = request.form.get("class_year", "").strip()
        class_year = int(class_year_raw) if class_year_raw.isdigit() else None
        if not name:
            flash("Founder name is required.", "error")
            return redirect(url_for("company_detail", company_id=company_id))
        db.add_founder(company_id, name, class_year)
        flash(f"Added {name} as a founder.", "success")
        return redirect(url_for("company_detail", company_id=company_id))

    @app.route("/founder/<int:founder_id>/edit", methods=["POST"])
    def edit_founder_route(founder_id: int):
        name = request.form.get("name", "").strip()
        class_year_raw = request.form.get("class_year", "").strip()
        class_year = int(class_year_raw) if class_year_raw.isdigit() else None
        if not name:
            flash("Founder name is required.", "error")
            return redirect(request.referrer or url_for("index"))
        db.update_founder(founder_id, name, class_year)
        flash("Founder updated.", "success")
        return redirect(request.referrer or url_for("index"))

    @app.route("/round/<int:round_id>/edit", methods=["POST"])
    def edit_round_route(round_id: int):
        round_type = request.form.get("round_type", "").strip() or None
        amount_raw = request.form.get("amount_usd", "").strip()
        amount_usd = int(amount_raw) if amount_raw.isdigit() else None
        announced_date = request.form.get("announced_date", "").strip() or None
        investors = request.form.get("investors", "").strip() or None
        db.update_funding_round(round_id, round_type, amount_usd, announced_date, investors)
        flash("Funding round updated.", "success")
        return redirect(request.referrer or url_for("index"))

    return app


def start_server(config: Dict[str, Any]) -> str:
    """Start the Werkzeug server in a daemon thread. Returns the base URL."""
    global _server, _thread
    if _server is not None:
        return f"http://127.0.0.1:{PORT}"

    app = create_app(config)
    _server = make_server("127.0.0.1", PORT, app, threaded=True)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    return f"http://127.0.0.1:{PORT}"


def open_dashboard(config: Dict[str, Any]) -> None:
    """Start the server (if needed) and open the dashboard in a browser."""
    url = start_server(config)
    webbrowser.open(url)
