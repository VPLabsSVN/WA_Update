from __future__ import annotations
import inspect
import hashlib
import os
import re
import socket
import threading
import time
import csv
import calendar
import json
import smtplib
import glob
from email.message import EmailMessage

try:
    from receipt_printer import ReceiptPrinterConfig, print_receipt, extract_fiscal_marks
except Exception as _receipt_import_error:
    ReceiptPrinterConfig = None
    print_receipt = None
    extract_fiscal_marks = None
    print(f"[RECEIPT PRINTER] Import failed: {_receipt_import_error}")
from decimal import Decimal, ROUND_HALF_UP
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional

import requests
from kivy.app import App
from kivy.clock import Clock
from kivy.uix.label import Label
from kivy.uix.boxlayout import BoxLayout

import Panel as base

import sys
from datetime import datetime

LOG_FILE = "/home/wa_stopshop_mb/WA/Main_program/Fis/panel_runtime_log.txt"


# ---- RECEIPT PRINTER SETTINGS ----
# SK1-311SF4-Q-M-SP supports USB/RS232. Use usb_file on Raspberry when it appears as /dev/usb/lp0.
# For serial/COM usage, set RECEIPT_PRINTER_CONNECTION = "serial" and RECEIPT_PRINTER_DEVICE = "COM3" or "/dev/ttyUSB0".
RECEIPT_PRINTER_ENABLED = True
RECEIPT_PRINTER_CONNECTION = "usb_file"
RECEIPT_PRINTER_DEVICE = "/dev/usb/lp0"
RECEIPT_PRINTER_SERIAL_BAUDRATE = 115200
RECEIPT_PRINTER_WAIT_FOR_FISCAL_SECONDS = 8.0
RECEIPT_PRINTER_TAX_NUMBER = "88720098"
RECEIPT_PRINTER_BUSINESS_NAME = "Cvetličarna Tulipan"
RECEIPT_PRINTER_OPERATOR_NAME = "World Automatics d.o.o."
RECEIPT_PRINTER_ADDRESS_LINE = "Župečja vas 6A, 2324 Lovrenc na Dravskem polju"
RECEIPT_PRINTER_QR_MODULE_SIZE = 4
RECEIPT_PRINTER_CUT_PAPER = True


# ---- MONTHLY INVOICE CSV / EMAIL REPORTING ----
# Every paid/fiscalized invoice is also written to a monthly CSV file.
# At the beginning of a new month, the app tries to send the completed previous month by email.
MONTHLY_INVOICE_REPORTS_ENABLED = True
MONTHLY_INVOICE_REPORT_DIR = os.path.join(os.path.dirname(LOG_FILE), "monthly_invoice_reports")
MONTHLY_INVOICE_SENT_MARKER_FILE = os.path.join(MONTHLY_INVOICE_REPORT_DIR, "monthly_invoice_email_sent.json")

# Test recipient requested by the user. Change this later for production.
MONTHLY_INVOICE_EMAIL_TO = "vid.pjong@gmail.com"

# Email sending stays disabled until SMTP credentials are entered.
# Titan Email SMTP defaults.
# Fill MONTHLY_INVOICE_SMTP_PASSWORD and set MONTHLY_INVOICE_EMAIL_ENABLED = True to send.
# Titan supports smtp.titan.email with either:
#   - port 465 + SSL/TLS  -> MONTHLY_INVOICE_SMTP_USE_TLS = False
#   - port 587 + STARTTLS -> MONTHLY_INVOICE_SMTP_USE_TLS = True
MONTHLY_INVOICE_EMAIL_ENABLED = True
MONTHLY_INVOICE_EMAIL_FROM = "vid.pejovnik@worldautomatics.com"
MONTHLY_INVOICE_SMTP_HOST = "smtp.titan.email"
MONTHLY_INVOICE_SMTP_PORT = 465
MONTHLY_INVOICE_SMTP_USERNAME = "vid.pejovnik@worldautomatics.com"
MONTHLY_INVOICE_SMTP_PASSWORD = "Tomaz123321!"
MONTHLY_INVOICE_SMTP_USE_TLS = False

# Test switch: when True, the app sends a combined CSV with all invoice rows on startup.
# Keep this False in production, otherwise it sends the all-invoices test report every startup.
MONTHLY_INVOICE_TEST_SEND_ALL_ON_STARTUP = False
MONTHLY_INVOICE_ALL_REPORT_FILENAME = "invoices_all_test_export.csv"

# Safety import: also scan older txt/json invoice logs and copy successful payments into monthly CSVs.
# This is safe to leave enabled because duplicate invoice/tax rows are skipped.
MONTHLY_INVOICE_IMPORT_OLD_TXT_ENABLED = True
MONTHLY_INVOICE_OLD_TXT_IMPORTED_MARKER_FILE = os.path.join(MONTHLY_INVOICE_REPORT_DIR, "old_txt_invoice_import_last.json")

MONTHLY_INVOICE_CSV_FIELDS = [
    "invoice_number",
    "issued_time",
    "amount",
    "tax_rate",
    "taxable_amount",
    "tax_amount",
]
_MONTHLY_INVOICE_REPORT_LOCK = threading.RLock()


def _invoice_month_from_iso(issue_datetime_iso: str):
    try:
        dt = datetime.fromisoformat(str(issue_datetime_iso).replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now()
    return dt.year, dt.month


def _monthly_invoice_csv_path(year: int, month: int) -> str:
    return os.path.join(MONTHLY_INVOICE_REPORT_DIR, f"invoices_{int(year):04d}_{int(month):02d}.csv")


def _all_invoices_csv_path() -> str:
    return os.path.join(MONTHLY_INVOICE_REPORT_DIR, MONTHLY_INVOICE_ALL_REPORT_FILENAME)



def _old_invoice_log_candidate_paths() -> list[str]:
    """Return possible old/current txt invoice log paths.

    The most useful old source is Panel.PAYMENT_LOG_FILE, which stores JSON lines
    written by append_payment_terminal_log().  LOG_FILE is also included because
    TeeLogger may contain pasted/printed JSON records in some deployments.
    """
    paths: list[str] = []

    def _add(path):
        if not path:
            return
        try:
            normalized = os.path.abspath(str(path))
        except Exception:
            normalized = str(path)
        if normalized not in paths:
            paths.append(normalized)

    _add(getattr(base, "PAYMENT_LOG_FILE", ""))
    _add(os.path.join(os.path.dirname(LOG_FILE), "payment_terminal_log.txt"))
    _add(LOG_FILE)
    return paths


def _json_from_log_line(line: str):
    text = str(line or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    # Runtime logs may prefix JSON with text. Try to parse the first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start:end + 1]
        try:
            return json.loads(candidate)
        except Exception:
            return None
    return None


def _record_is_successful_invoice(record: dict) -> bool:
    if not isinstance(record, dict):
        return False
    if not record.get("invoice_number"):
        return False

    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    outcome = str(result.get("outcome") or record.get("outcome") or "").lower()
    kind = str(record.get("kind") or "").lower()
    payment_status = str(record.get("payment_status") or "").lower()

    if outcome == "committed":
        return True
    if payment_status == "paid":
        return True
    if kind in {"payment_terminal_result", "payment_result"} and outcome == "committed":
        return True
    return False


def _record_to_invoice_args(record: dict):
    invoice_number = str(record.get("invoice_number") or "").strip()
    issue_datetime_iso = str(record.get("issue_datetime_iso") or record.get("issued_time") or record.get("logged_at") or "").strip()
    if not issue_datetime_iso:
        issue_datetime_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    try:
        total = float(record.get("total") or record.get("amount") or 0.0)
    except Exception:
        total = 0.0
    items = record.get("items") if isinstance(record.get("items"), list) else []
    if not total and items:
        try:
            total = sum(float(it.get("price", 0.0)) for it in items if isinstance(it, dict))
        except Exception:
            total = 0.0
    return invoice_number, issue_datetime_iso, total, items


def import_old_txt_invoices_to_monthly_csv() -> int:
    """Import successful invoice rows from old txt/json logs into monthly CSVs.

    It can be run repeatedly. append_monthly_invoice_csv() skips duplicate
    invoice_number + tax_rate rows, so this acts as a safety net for older logs.
    """
    if not MONTHLY_INVOICE_REPORTS_ENABLED or not MONTHLY_INVOICE_IMPORT_OLD_TXT_ENABLED:
        return 0

    imported = 0
    scanned_records = 0
    for path in _old_invoice_log_candidate_paths():
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    record = _json_from_log_line(line)
                    if not isinstance(record, dict):
                        continue
                    scanned_records += 1
                    if not _record_is_successful_invoice(record):
                        continue
                    invoice_number, issue_datetime_iso, total, items = _record_to_invoice_args(record)
                    if not invoice_number:
                        continue
                    append_monthly_invoice_csv(invoice_number, issue_datetime_iso, total, items)
                    imported += 1
        except Exception as e:
            print(f"[OLD TXT INVOICE IMPORT] Could not scan {path}: {e}")

    if imported:
        print(f"[OLD TXT INVOICE IMPORT] Imported/checked {imported} successful old invoice record(s).")
    else:
        print(f"[OLD TXT INVOICE IMPORT] No old successful invoice records imported. Scanned JSON records: {scanned_records}.")
    return imported


def build_all_invoices_test_csv() -> str:
    """Build one combined CSV from all monthly invoice CSV files.

    This is mainly for email testing and accountant export checks. It combines rows
    from invoices_YYYY_MM.csv files and de-duplicates identical rows.
    """
    import_old_txt_invoices_to_monthly_csv()
    os.makedirs(MONTHLY_INVOICE_REPORT_DIR, exist_ok=True)
    out_path = _all_invoices_csv_path()
    monthly_paths = sorted(glob.glob(os.path.join(MONTHLY_INVOICE_REPORT_DIR, "invoices_????_??.csv")))

    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()

    for path in monthly_paths:
        # Avoid accidentally reading the combined test export if the filename is changed later.
        if os.path.abspath(path) == os.path.abspath(out_path):
            continue
        try:
            with open(path, "r", newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    clean = {field: str(row.get(field, "")) for field in MONTHLY_INVOICE_CSV_FIELDS}
                    key = tuple(clean.get(field, "") for field in MONTHLY_INVOICE_CSV_FIELDS)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(clean)
        except Exception as e:
            print(f"[MONTHLY EMAIL TEST] Could not read {path}: {e}")

    def _sort_key(row: dict[str, str]):
        return (str(row.get("issued_time", "")), str(row.get("invoice_number", "")), str(row.get("tax_rate", "")))

    rows.sort(key=_sort_key)

    with _MONTHLY_INVOICE_REPORT_LOCK:
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=MONTHLY_INVOICE_CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    print(f"[MONTHLY EMAIL TEST] Built all-invoices CSV: {out_path} ({len(rows)} row(s)).")
    return out_path


def _smtp_settings_ready() -> bool:
    if not MONTHLY_INVOICE_EMAIL_ENABLED:
        print("[MONTHLY EMAIL] Email sending disabled. Set MONTHLY_INVOICE_EMAIL_ENABLED = True to send.")
        return False
    if not MONTHLY_INVOICE_SMTP_HOST or not MONTHLY_INVOICE_SMTP_USERNAME or not MONTHLY_INVOICE_SMTP_PASSWORD:
        print("[MONTHLY EMAIL] Missing SMTP settings. Fill MONTHLY_INVOICE_SMTP_USERNAME/PASSWORD and enable email sending.")
        return False
    return True


def _send_invoice_csv_attachment(path: str, *, subject: str, body: str) -> bool:
    if not os.path.exists(path):
        print(f"[MONTHLY EMAIL] Attachment not found: {path}")
        return False

    if not _smtp_settings_ready():
        return False

    sender = MONTHLY_INVOICE_EMAIL_FROM or MONTHLY_INVOICE_SMTP_USERNAME
    recipient = MONTHLY_INVOICE_EMAIL_TO

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)

    with open(path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="text",
            subtype="csv",
            filename=os.path.basename(path),
        )

    try:
        if MONTHLY_INVOICE_SMTP_USE_TLS:
            with smtplib.SMTP(MONTHLY_INVOICE_SMTP_HOST, int(MONTHLY_INVOICE_SMTP_PORT), timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(MONTHLY_INVOICE_SMTP_USERNAME, MONTHLY_INVOICE_SMTP_PASSWORD)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(MONTHLY_INVOICE_SMTP_HOST, int(MONTHLY_INVOICE_SMTP_PORT), timeout=20) as smtp:
                smtp.login(MONTHLY_INVOICE_SMTP_USERNAME, MONTHLY_INVOICE_SMTP_PASSWORD)
                smtp.send_message(msg)
        print(f"[MONTHLY EMAIL] Sent {path} to {recipient}")
        return True
    except Exception as e:
        print(f"[MONTHLY EMAIL] Failed to send {path}: {e}")
        return False


def send_all_invoices_test_email() -> bool:
    path = build_all_invoices_test_csv()
    return _send_invoice_csv_attachment(
        path,
        subject="Bouquetmat ALL invoices test report",
        body=(
            "Attached is the Bouquetmat all-invoices CSV test report.\n\n"
            "This startup test report combines all invoices_YYYY_MM.csv files currently found "
            "in the monthly_invoice_reports folder.\n"
        ),
    )


def _money_decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _tax_rate_key(value) -> str:
    try:
        d = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return format(d, "f").rstrip("0").rstrip(".") or "0"
    except Exception:
        return "0"


def _build_monthly_invoice_rows(invoice_number: str, issue_datetime_iso: str, total: float, items: list) -> list[dict[str, str]]:
    by_tax: dict[str, Decimal] = {}

    for it in list(items or []):
        rate_key = _tax_rate_key(it.get("tax_pct", 0.0))
        by_tax.setdefault(rate_key, Decimal("0.00"))
        by_tax[rate_key] += _money_decimal(it.get("price", 0.0))

    if not by_tax:
        by_tax[_tax_rate_key(0.0)] = _money_decimal(total)

    rows: list[dict[str, str]] = []
    for rate_key, gross in sorted(by_tax.items(), key=lambda kv: Decimal(kv[0])):
        rate = Decimal(rate_key)
        if rate == 0:
            taxable = gross
            tax_amount = Decimal("0.00")
        else:
            taxable = (gross / (Decimal("1.00") + rate / Decimal("100.00"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            tax_amount = (gross - taxable).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        rows.append({
            "invoice_number": str(invoice_number),
            "issued_time": str(issue_datetime_iso),
            "amount": format(gross.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f"),
            "tax_rate": rate_key,
            "taxable_amount": format(taxable, "f"),
            "tax_amount": format(tax_amount, "f"),
        })
    return rows


def append_monthly_invoice_csv(invoice_number: str, issue_datetime_iso: str, total: float, items: list) -> None:
    if not MONTHLY_INVOICE_REPORTS_ENABLED:
        return
    try:
        year, month = _invoice_month_from_iso(issue_datetime_iso)
        os.makedirs(MONTHLY_INVOICE_REPORT_DIR, exist_ok=True)
        path = _monthly_invoice_csv_path(year, month)
        rows = _build_monthly_invoice_rows(invoice_number, issue_datetime_iso, total, items)

        with _MONTHLY_INVOICE_REPORT_LOCK:
            existing_keys: set[tuple[str, str]] = set()
            if os.path.exists(path):
                try:
                    with open(path, "r", newline="", encoding="utf-8-sig") as f:
                        for row in csv.DictReader(f):
                            existing_keys.add((str(row.get("invoice_number", "")), str(row.get("tax_rate", ""))))
                except Exception as e:
                    print(f"[MONTHLY CSV] Could not read existing CSV for duplicate check: {e}")

            file_exists = os.path.exists(path) and os.path.getsize(path) > 0
            with open(path, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=MONTHLY_INVOICE_CSV_FIELDS)
                if not file_exists:
                    writer.writeheader()
                written = 0
                for row in rows:
                    key = (str(row.get("invoice_number", "")), str(row.get("tax_rate", "")))
                    if key in existing_keys:
                        continue
                    writer.writerow(row)
                    existing_keys.add(key)
                    written += 1
            print(f"[MONTHLY CSV] Invoice {invoice_number} written to {path} ({written} row(s)).")
    except Exception as e:
        print(f"[MONTHLY CSV] Failed to write invoice {invoice_number}: {e}")


def _load_monthly_email_sent_markers() -> dict:
    try:
        with open(MONTHLY_INVOICE_SENT_MARKER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_monthly_email_sent_markers(markers: dict) -> None:
    os.makedirs(MONTHLY_INVOICE_REPORT_DIR, exist_ok=True)
    tmp = MONTHLY_INVOICE_SENT_MARKER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(markers, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MONTHLY_INVOICE_SENT_MARKER_FILE)


def send_monthly_invoice_csv_email(year: int, month: int) -> bool:
    path = _monthly_invoice_csv_path(year, month)
    key = f"{int(year):04d}-{int(month):02d}"

    if not os.path.exists(path):
        print(f"[MONTHLY EMAIL] No CSV exists for {key}: {path}")
        return False

    return _send_invoice_csv_attachment(
        path,
        subject=f"Bouquetmat monthly invoice report {key}",
        body=(
            f"Attached is the Bouquetmat monthly invoice CSV report for {key}.\n\n"
            f"File: {os.path.basename(path)}\n"
        ),
    )


def send_due_monthly_invoice_reports() -> None:
    """Send the completed previous month once. Runs safely on startup and every few hours."""
    if not MONTHLY_INVOICE_REPORTS_ENABLED:
        return
    import_old_txt_invoices_to_monthly_csv()
    now = datetime.now()
    prev_year = now.year
    prev_month = now.month - 1
    if prev_month <= 0:
        prev_year -= 1
        prev_month = 12

    key = f"{prev_year:04d}-{prev_month:02d}"
    markers = _load_monthly_email_sent_markers()
    if markers.get(key):
        return

    csv_path = _monthly_invoice_csv_path(prev_year, prev_month)
    if not os.path.exists(csv_path):
        return

    if send_monthly_invoice_csv_email(prev_year, prev_month):
        markers[key] = datetime.now().isoformat(timespec="seconds")
        _save_monthly_email_sent_markers(markers)

class TeeLogger:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

def setup_file_logging():
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        logfile = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        logfile.write("\n" + "=" * 80 + "\n")
        logfile.write(f"START {datetime.now().isoformat(timespec='seconds')}\n")
        logfile.write("=" * 80 + "\n")
        sys.stdout = TeeLogger(sys.__stdout__, logfile)
        sys.stderr = TeeLogger(sys.__stderr__, logfile)
        print(f"[LOG] Writing runtime log to: {LOG_FILE}")
    except Exception as e:
        print(f"[LOG] Failed to start file logging: {e}")

def _backend_force_white_keyboard_widgets(app):
    root = getattr(app, "root", None)
    if root is None:
        return

    def walk(widget, in_keyboard=False):
        try:
            if widget.__class__.__name__ in ("AdminKeyboard", "ReservationNumpad"):
                in_keyboard = True
        except Exception:
            pass

        if in_keyboard:
            try:
                if isinstance(widget, base.HoverButton):
                    widget.color = (1, 1, 1, 1)
                    widget.disabled_color = (1, 1, 1, 0.85)
                    try:
                        widget.canvas.ask_update()
                    except Exception:
                        pass
            except Exception:
                pass

        for child in getattr(widget, "children", []):
            walk(child, in_keyboard)

    for screen_name in ("admin_login", "reservation", "admin_edit_item"):
        try:
            screen = root.get_screen(screen_name)
        except Exception:
            continue
        walk(screen)

base.I18N.setdefault("reserved", {"en": "Reserved", "si": "Rezervirano", "de": "Reserviert", "hr": "Rezervirano"})
base.I18N.setdefault("reserved_not_for_sale", {"en": "Reserved - not available", "si": "Rezervirano - ni na voljo", "de": "Reserviert - nicht verfügbar", "hr": "Rezervirano - nije dostupno"})

base.ICON_HR = getattr(base, "ICON_HR", os.path.join(getattr(base, "PANEL_DIR", "."), "flag_hr.png"))

_CROATIAN_BACKEND_OVERRIDES = {
    "reserved": "Rezervirano",
    "reserved_not_for_sale": "Rezervirano - nije dostupno",
    "module_slots": "Modul {panel_type} - {slots} mjesta",
    "reservation_enter_code": "Unesite kod rezervacije",
    "reservation_verify": "Provjeri",
    "reservation_collect": "Preuzmi",
    "reservation_clear": "Obriši",
    "reservation_code_invalid": "Kod rezervacije nije valjan.",
    "reservation_code_expired": "Kod rezervacije je istekao.",
    "reservation_code_redeemed": "Rezervacija je već iskorištena.",
    "reservation_code_claimed": "Rezervacija se trenutno obrađuje. Pokušajte ponovno uskoro.",
    "reservation_ready": "Rezervacija je valjana.",
    "reservation_collecting": "Preuzimanje rezervacije...",
    "reservation_success": "Rezervacija preuzeta.",
    "reservation_backend_error": "Greška poslužitelja rezervacija.",
}
for _k, _v in getattr(base, "I18N", {}).items():
    if isinstance(_v, dict) and "hr" not in _v:
        _v["hr"] = _CROATIAN_BACKEND_OVERRIDES.get(_k, _v.get("en", next(iter(_v.values()), _k)))

_BACKEND_HR_EXTRA = {
    "bouquet": "Buket",
    "instructions_body": getattr(base, "INSTRUCTIONS_BODY_HR", ""),
    "terms_body": getattr(base, "TERMS_BODY_HR", ""),
    "printing_receipt": "Račun",
    "reprint_receipt": "Račun",
    "not_available": "Nije dostupno (već kupljeno)",
    "add_to_cart": "Dodaj u košaricu",
    "cart_empty": "Košarica je prazna. Vratite se i odaberite proizvod.",
    "clear_cart": "Očisti",
    "keyboard_clear": "Obriši",
    "admin_accounts_missing": "Datoteka s admin računima nedostaje ili je prazna",
    "access_denied": "Pristup odbijen",
    "door_opening_now": "Otvaranje vrata...",
    "door_open_success": "Vrata otvorena",
    "door_open_failed": "Vrata se nisu otvorila. Dodirnite ponovno.",
    "door_open_partial": "Neka vrata su se otvorila, neka nisu.",
    "admin_tap_reopen": "Dodirnite za ponovno otvaranje vrata",
    "admin_role_manufacturer": "Proizvođač",
    "admin_role_owner": "Vlasnik",
    "admin_role_worker": "Radnik",
    "re_enable_all": "Otvori sve",
    "re_enable_this": "Otvori",
    "receipt": "Račun",
    "status_enabled": "Omogućeno",
    "status_disabled": "Onemogućeno",
    "payment_terminal_starting": "Povezivanje s platnim terminalom...",
    "please_pay_on_terminal": "Molimo dovršite plaćanje na terminalu.",
    "payment_approved": "Plaćanje potvrđeno.",
    "payment_failed": "Plaćanje nije uspjelo.",
    "payment_terminal_not_ready": "Platni terminal nije spreman.",
    "payment_cancelled_or_declined": "Plaćanje je otkazano ili odbijeno.",
    "relay_timeout": "Odabrana vrata ne odgovaraju.",
    "reopen_door": "Otvori",
    "buy_another": "Kupi",
    "printing_receipt": "Račun",
    "reprint_receipt": "Račun",
}
for _k, _v in getattr(base, "I18N", {}).items():
    if isinstance(_v, dict):
        if _k in _BACKEND_HR_EXTRA:
            _v["hr"] = _BACKEND_HR_EXTRA[_k]
        elif "hr" not in _v:
            _v["hr"] = _v.get("en", next(iter(_v.values()), _k))


# =========================================================
# CENTRAL BACKEND SETTINGS
# =========================================================
BACKEND_ENABLED = True
BACKEND_BASE_URL = "http://46.225.177.107"
BACKEND_PANEL_KEY = ""
BACKEND_SOFTWARE_VERSION = "0.1.0"
BACKEND_PANEL_ID_OVERRIDE = "Prot"   # e.g. panel-main-01
BACKEND_PANEL_NAME_OVERRIDE = "MB" # e.g. Main Panel Shop Center
BACKEND_HEARTBEAT_SECONDS = 15
BACKEND_COMMAND_POLL_SECONDS = 5
BACKEND_ADMIN_SYNC_SECONDS = 20
BACKEND_CONFIG_REFRESH_SECONDS = 5
BACKEND_STARTUP_RETRY_SECONDS = 3

# Manual reservation flow
RESERVATION_REQUEST_TIMEOUT_S = 5.0
RESERVATION_HOLD_SECONDS = 60
COMPONENT_PROBE_TIMEOUT_S = 1.0
REMOTE_PANEL_STALE_SECONDS = 45
REMOTE_PANEL_PRUNE_INTERVAL_S = 5


def _make_backend_panel_id(position: int) -> str:
    position = int(position)
    if position == 0:
        return "panel-0"
    if position < 0:
        return f"panel-neg{abs(position)}"
    return f"panel-{position}"


def _make_backend_panel_name(position: int) -> str:
    position = int(position)
    if position == 0:
        return "Main Panel"
    if position < 0:
        return f"Side Panel Left {abs(position)}"
    return f"Side Panel Right {position}"


BACKEND_PANEL_ID = (BACKEND_PANEL_ID_OVERRIDE.strip() or _make_backend_panel_id(int(getattr(base, "PANEL_POSITION", 0))))
BACKEND_PANEL_NAME = (BACKEND_PANEL_NAME_OVERRIDE.strip() or _make_backend_panel_name(int(getattr(base, "PANEL_POSITION", 0))))


# =========================================================
# Small backend clients
# =========================================================
class PanelBackendClient:
    def __init__(
        self,
        *,
        base_url: str,
        panel_id: str,
        panel_name: str,
        panel_type: str,
        position: int,
        software_version: str = "0.1.0",
        panel_key: str = "",
        timeout: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.panel_id = panel_id
        self.panel_name = panel_name
        self.panel_type = panel_type
        self.position = int(position)
        self.software_version = software_version
        self.panel_key = panel_key or ""
        self.timeout = float(timeout)
        self.last_command_version = 0
        self.session = requests.Session()

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.panel_key:
            headers["X-Panel-Key"] = self.panel_key
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _local_ip(self) -> str:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            sock.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def register(self) -> dict[str, Any]:
        payload = {
            "panel_id": self.panel_id,
            "panel_name": self.panel_name,
            "panel_type": self.panel_type,
            "position": self.position,
            "local_ip": self._local_ip(),
            "software_version": self.software_version,
        }
        r = self.session.post(
            self._url("/api/panels/register"),
            json=payload,
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def heartbeat(
        self,
        *,
        status: str = "online",
        current_screen: str | None = None,
        cart_count: int = 0,
        payment_terminal_ready: bool = False,
        component_statuses: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "status": status,
            "current_screen": current_screen,
            "cart_count": int(cart_count),
            "payment_terminal_ready": bool(payment_terminal_ready),
            "component_statuses": component_statuses or {},
        }
        r = self.session.post(
            self._url(f"/api/panels/{self.panel_id}/heartbeat"),
            json=payload,
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def get_config(self) -> dict[str, Any]:
        r = self.session.get(
            self._url(f"/api/panels/{self.panel_id}/config"),
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def sync_items(self, items: list[dict[str, Any]], *, replace_missing: bool = True) -> dict[str, Any]:
        payload = {
            "replace_missing": bool(replace_missing),
            "items": [
                {
                    "panel_position": int(it.get("panel_position", self.position)),
                    "item_id": int(it["id"]),
                    "label": str(it.get("label", it["id"])),
                    "price": float(it.get("price", 0.0)),
                    "tax_pct": float(it.get("tax_pct", 0.0)),
                    "enabled": bool(it.get("enabled", True)),
                }
                for it in items
                if it is not None and it.get("id") is not None
            ],
        }
        r = self.session.post(
            self._url(f"/api/panels/{self.panel_id}/items/sync"),
            json=payload,
            headers=self.headers,
            timeout=max(self.timeout, 10.0),
        )
        r.raise_for_status()
        return r.json()

    def sync_admin_users(self, users: list[dict[str, Any]], *, replace_missing: bool = True) -> dict[str, Any]:
        payload = {
            "replace_missing": bool(replace_missing),
            "users": [
                {
                    "username": str(u.get("username", "")).strip(),
                    "password": str(u.get("password", "")),
                    "role": str(u.get("role", "worker")),
                    "is_active": bool(u.get("is_active", True)),
                }
                for u in users
                if str(u.get("username", "")).strip()
            ],
        }
        r = self.session.post(
            self._url(f"/api/panels/{self.panel_id}/admin-users/sync"),
            json=payload,
            headers=self.headers,
            timeout=max(self.timeout, 10.0),
        )
        r.raise_for_status()
        return r.json()

    def post_event(self, event_type: str, *, severity: str = "info", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {
            "panel_id": self.panel_id,
            "event_type": event_type,
            "severity": severity,
            "payload": payload or {},
        }
        r = self.session.post(
            self._url("/api/events"),
            json=body,
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def post_sale(
        self,
        *,
        invoice_number: str,
        issue_datetime_iso: str,
        total_amount: float,
        payment_status: str,
        fiscal_status: str,
        items: list[dict[str, Any]],
        currency: str = "EUR",
    ) -> dict[str, Any]:
        body = {
            "panel_id": self.panel_id,
            "invoice_number": invoice_number,
            "issue_datetime_iso": issue_datetime_iso,
            "currency": currency,
            "total_amount": float(total_amount),
            "payment_status": payment_status,
            "fiscal_status": fiscal_status,
            "items": [
                {
                    "item_id": int(it["id"]),
                    "panel_position": int(it.get("panel_position", self.position)),
                    "label": str(it.get("label", it["id"])),
                    "quantity": int(it.get("quantity", 1)),
                    "unit_price": float(it.get("price", 0.0)),
                    "tax_pct": float(it.get("tax_pct", 0.0)),
                }
                for it in items
                if it is not None and it.get("id") is not None
            ],
        }
        r = self.session.post(
            self._url("/api/sales"),
            json=body,
            headers=self.headers,
            timeout=max(self.timeout, 10.0),
        )
        r.raise_for_status()
        return r.json()

    def post_door_event(
        self,
        *,
        item_id: int,
        panel_position: int,
        action: str,
        success: bool,
        error_text: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "panel_id": self.panel_id,
            "item_id": int(item_id),
            "panel_position": int(panel_position),
            "action": action,
            "success": bool(success),
            "error_text": error_text,
        }
        r = self.session.post(
            self._url("/api/door-events"),
            json=body,
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def poll_commands(self) -> dict[str, Any]:
        r = self.session.get(
            self._url(f"/api/panels/{self.panel_id}/commands"),
            params={"since_version": int(self.last_command_version)},
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        self.last_command_version = int(data.get("latest_version", self.last_command_version) or self.last_command_version)
        return data

    def ack_commands(self, versions: list[int]) -> dict[str, Any]:
        body = {"command_versions": [int(v) for v in versions]}
        r = self.session.post(
            self._url(f"/api/panels/{self.panel_id}/commands/ack"),
            json=body,
            headers=self.headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()


@dataclass
class ReservationClientConfig:
    base_url: str = BACKEND_BASE_URL
    panel_key: str = BACKEND_PANEL_KEY
    timeout: float = RESERVATION_REQUEST_TIMEOUT_S


class ReservationBackendClient:
    def __init__(self, config: ReservationClientConfig | None = None):
        self.config = config or ReservationClientConfig()
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.panel_key:
            headers["X-Panel-Key"] = self.config.panel_key
        return headers

    def _url(self, path: str) -> str:
        return self.config.base_url.rstrip("/") + path

    def verify(self, *, code: str, panel_id: Optional[str] = None) -> dict[str, Any]:
        payload = {"code": str(code).strip(), "panel_id": panel_id}
        resp = self.session.post(
            self._url("/api/reservations/verify"),
            json=payload,
            headers=self._headers(),
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def redeem(self, *, code: str, panel_id: Optional[str] = None, redeemed_by: str = "panel") -> dict[str, Any]:
        payload = {
            "code": str(code).strip(),
            "panel_id": panel_id,
            "redeemed_by": redeemed_by,
        }
        resp = self.session.post(
            self._url("/api/reservations/redeem"),
            json=payload,
            headers=self._headers(),
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def claim(self, *, code: str, panel_id: Optional[str] = None, claimed_by: str = "panel", hold_seconds: int = RESERVATION_HOLD_SECONDS) -> dict[str, Any]:
        payload = {
            "code": str(code).strip(),
            "panel_id": panel_id,
            "claimed_by": claimed_by,
            "hold_seconds": int(hold_seconds),
        }
        resp = self.session.post(
            self._url("/api/reservations/claim"),
            json=payload,
            headers=self._headers(),
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def confirm(self, *, code: str, claim_token: str, panel_id: Optional[str] = None, redeemed_by: str = "panel") -> dict[str, Any]:
        payload = {
            "code": str(code).strip(),
            "claim_token": str(claim_token),
            "panel_id": panel_id,
            "redeemed_by": redeemed_by,
        }
        resp = self.session.post(
            self._url("/api/reservations/confirm"),
            json=payload,
            headers=self._headers(),
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def release(self, *, code: str, claim_token: str, panel_id: Optional[str] = None, released_by: str = "panel", release_reason: str | None = None) -> dict[str, Any]:
        payload = {
            "code": str(code).strip(),
            "claim_token": str(claim_token),
            "panel_id": panel_id,
            "released_by": released_by,
            "release_reason": release_reason,
        }
        resp = self.session.post(
            self._url("/api/reservations/release"),
            json=payload,
            headers=self._headers(),
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        return resp.json()


# =========================================================
# Patch base I18N / KV / save hook
# =========================================================
base.I18N.update(
    {
        "reservation_enter_code": {
            "en": "Enter reservation code",
            "si": "Vnesite kodo rezervacije",
            "de": "Reservierungscode eingeben",
        },
        "reservation_verify": {
            "en": "Verify",
            "si": "Preveri",
            "de": "Prüfen",
        },
        "reservation_collect": {
            "en": "Collect",
            "si": "Prevzem",
            "de": "Abholen",
        },
        "reservation_clear": {
            "en": "Clear",
            "si": "Počisti",
            "de": "Leeren",
        },
        "reservation_code_invalid": {
            "en": "Reservation code is invalid.",
            "si": "Koda rezervacije ni veljavna.",
            "de": "Reservierungscode ist ungültig.",
        },
        "reservation_code_expired": {
            "en": "Reservation code has expired.",
            "si": "Koda rezervacije je potekla.",
            "de": "Reservierungscode ist abgelaufen.",
        },
        "reservation_code_redeemed": {
            "en": "Reservation was already used.",
            "si": "Rezervacija je že bila uporabljena.",
            "de": "Reservierung wurde bereits verwendet.",
        },
        "reservation_code_claimed": {
            "en": "Reservation is currently being processed. Please try again shortly.",
            "si": "Rezervacija se trenutno obdeluje. Poskusite znova čez trenutek.",
            "de": "Reservierung wird gerade verarbeitet. Bitte gleich erneut versuchen.",
        },
        "reservation_ready": {
            "en": "Reservation is valid.",
            "si": "Rezervacija je veljavna.",
            "de": "Reservierung ist gültig.",
        },
        "reservation_collecting": {
            "en": "Collecting reservation...",
            "si": "Prevzem rezervacije...",
            "de": "Reservierung wird abgeholt...",
        },
        "reservation_success": {
            "en": "Reservation collected.",
            "si": "Rezervacija prevzeta.",
            "de": "Reservierung abgeholt.",
        },
        "reservation_backend_error": {
            "en": "Reservation backend error.",
            "si": "Napaka strežnika rezervacij.",
            "de": "Fehler beim Reservierungsserver.",
        },
    }
)

# Croatian reservation translations added after base.I18N.update(), because update() replaces earlier keys.
_BACKEND_RESERVATION_HR = {
    "reservation_enter_code": "Unesite kod rezervacije",
    "reservation_verify": "Provjeri",
    "reservation_collect": "Preuzmi",
    "reservation_clear": "Obriši",
    "reservation_code_invalid": "Kod rezervacije nije valjan.",
    "reservation_code_expired": "Kod rezervacije je istekao.",
    "reservation_code_redeemed": "Rezervacija je već iskorištena.",
    "reservation_code_claimed": "Rezervacija se trenutno obrađuje. Pokušajte ponovno uskoro.",
    "reservation_ready": "Rezervacija je valjana.",
    "reservation_collecting": "Preuzimanje rezervacije...",
    "reservation_success": "Rezervacija preuzeta.",
    "reservation_backend_error": "Greška poslužitelja rezervacija.",
    "keyboard_clear": "Obriši",
}
for _k, _hr in _BACKEND_RESERVATION_HR.items():
    base.I18N.setdefault(_k, {})["hr"] = _hr

_RESERVATION_SCREEN_BLOCK = '''
<ReservationScreen>:
    name: "reservation"
    FullScreenFloat:
        FloatLayout:
            BoxLayout:
                orientation: "vertical"
                padding: [dp(30), TOP_PAD, dp(30), dp(30)]
                spacing: dp(20)
                size_hint: 1, 1

                Widget:
                    size_hint_y: None
                    height: dp(56)

                TitleLabel:
                    id: reservation_title
                    text: "Reservation"
                    size_hint_y: None
                    height: 0
                    opacity: 0

                BoxLayout:
                    size_hint_y: None
                    height: "80dp"
                    Widget:
                    RoundedTextInput:
                        id: reservation_code_input
                        hint_text: "Enter reservation code"
                        font_size: OTHER_TXT
                        fill_rgba: [0.2, 0.33, 0.23, 0.84]
                        border_rgba: [0.84, 0.71, 0.36, 0.95]
                        foreground_color: 1, 1, 1, 1
                        disabled_foreground_color: 1, 1, 1, 1
                        cursor_color: 1, 1, 1, 1
                        hint_text_color: 1, 1, 1, 0.78
                        hint_text_color_normal: 1, 1, 1, 0.78
                        hint_text_color_focus: 1, 1, 1, 0.78
                        selection_color: 1, 1, 1, 0.18
                        size_hint_x: None
                        width: min(dp(860), Window.width - dp(140))
                    Widget:

                Label:
                    id: reservation_status
                    text: ""
                    font_size: OTHER_TXT
                    bold: True
                    color: 1, 1, 1, 0.96
                    halign: "center"
                    valign: "middle"
                    text_size: self.size
                    size_hint_y: None
                    height: "120dp"

                Widget:
                    size_hint_y: 0.08

                AdminKeyboard:

                BoxLayout:
                    size_hint_y: None
                    height: "110dp"
                    spacing: dp(16)

                    NavIconButton:
                        id: reservation_back_btn
                        icon: app.ICON_BACK
                        text: "Back"
                        on_release: app.goto_menu()

                    NavIconButton:
                        id: reservation_home_btn
                        icon: app.ICON_HOME
                        text: "Home"
                        on_release: app.goto_menu()

                    NavIconButton:
                        id: reservation_collect_btn
                        icon: app.ICON_RESERVATION
                        text: "Collect"
                        on_release: app.reservation_collect()

        HeaderBar:
'''.strip("\n")

if "<ReservationScreen>:" in base.KV:
    base.KV = re.sub(
        r"<ReservationScreen>:\n.*?(?=\n<PriceCheckScreen>:)",
        _RESERVATION_SCREEN_BLOCK + "\n",
        base.KV,
        flags=re.S,
    )

_original_save_items_state = base.save_items_state

def _save_items_state_patched(items):
    _original_save_items_state(items)
    try:
        app = App.get_running_app()
        if app and hasattr(app, "backend_sync_items_async"):
            if not getattr(app, "_backend_applying_config", False):
                app.backend_sync_items_async()
    except Exception as e:
        print(f"[BACKEND] patched save sync failed: {e}")

base.save_items_state = _save_items_state_patched


class PanelBackendReservationApp(base.PanelApp):
    def build(self):
        root = super().build()
        try:
            rs = root.get_screen("reservation")
            if "reservation_body" not in rs.ids and "reservation_status" in rs.ids:
                rs.ids["reservation_body"] = rs.ids["reservation_status"]
        except Exception:
            pass
        for delay in (0, 0.05, 0.2, 0.8):
            try:
                Clock.schedule_once(lambda _dt, app=self: _backend_force_white_keyboard_widgets(app), delay)
            except Exception:
                pass
        return root

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.backend_client = None
        self.backend_ready = False
        self.backend_last_error = ""
        self.backend_config_version = 0
        self.server_reservation_codes: list[dict[str, Any]] = []
        self._backend_stop = False
        self._backend_applying_config = False
        self._backend_pending_sales: dict[str, dict[str, Any]] = {}
        self._admin_sync_last_mtime: float | None = None
        self._admin_sync_last_hash: str = ""

        self._verified_reservation: dict[str, Any] | None = None
        self._reservation_claim_token: str | None = None
        self._last_fiscal_status = "unknown"
        self._last_fiscal_error = ""
        self._fiscal_results_by_invoice: dict[str, dict[str, Any]] = {}
        self._last_invoice_number: str | None = None
        self._last_issue_datetime_iso: str | None = None
        self._last_purchase_total: float = 0.0
        self._receipt_print_lock = threading.RLock()
        self.reservation_client = ReservationBackendClient(
            ReservationClientConfig(
                base_url=BACKEND_BASE_URL,
                panel_key=BACKEND_PANEL_KEY,
                timeout=RESERVATION_REQUEST_TIMEOUT_S,
            )
        )

        if BACKEND_ENABLED:
            self.backend_client = PanelBackendClient(
                base_url=BACKEND_BASE_URL,
                panel_id=BACKEND_PANEL_ID,
                panel_name=BACKEND_PANEL_NAME,
                panel_type=self.panel_type,
                position=int(getattr(base, "PANEL_POSITION", 0)),
                software_version=BACKEND_SOFTWARE_VERSION,
                panel_key=BACKEND_PANEL_KEY,
            )

    def on_start(self):
        super().on_start()
        for delay in (0, 0.1, 0.3, 1.0):
            try:
                Clock.schedule_once(lambda _dt, app=self: _backend_force_white_keyboard_widgets(app), delay)
            except Exception:
                pass
        if int(getattr(base, "PANEL_POSITION", 0)) == 0:
            try:
                Clock.schedule_interval(self._remote_panel_prune_tick, REMOTE_PANEL_PRUNE_INTERVAL_S)
            except Exception:
                pass
        self._start_backend_threads()
        self._start_monthly_invoice_reporter()

    def on_stop(self):
        self._backend_stop = True
        super().on_stop()

    def _start_monthly_invoice_reporter(self):
        if not MONTHLY_INVOICE_REPORTS_ENABLED:
            return
        try:
            Clock.schedule_once(lambda _dt: self._run_old_txt_invoice_import(), 5.0)
            Clock.schedule_once(lambda _dt: self._run_monthly_invoice_report_tick(), 10.0)
            Clock.schedule_interval(lambda _dt: self._run_monthly_invoice_report_tick(), 6 * 60 * 60)
            if MONTHLY_INVOICE_TEST_SEND_ALL_ON_STARTUP:
                Clock.schedule_once(lambda _dt: self._run_all_invoices_test_email(), 20.0)
                print("[MONTHLY EMAIL TEST] Startup all-invoices test email scheduled.")
            print("[MONTHLY EMAIL] Monthly invoice reporter scheduled.")
        except Exception as e:
            print(f"[MONTHLY EMAIL] Could not schedule monthly invoice reporter: {e}")

    def _run_old_txt_invoice_import(self):
        try:
            threading.Thread(target=import_old_txt_invoices_to_monthly_csv, daemon=True).start()
        except Exception as e:
            print(f"[OLD TXT INVOICE IMPORT] Could not start import thread: {e}")

    def _run_monthly_invoice_report_tick(self):
        try:
            threading.Thread(target=send_due_monthly_invoice_reports, daemon=True).start()
        except Exception as e:
            print(f"[MONTHLY EMAIL] Tick failed: {e}")

    def _run_all_invoices_test_email(self):
        try:
            threading.Thread(target=send_all_invoices_test_email, daemon=True).start()
        except Exception as e:
            print(f"[MONTHLY EMAIL TEST] Startup test failed: {e}")

    # ---------- remote panel stale handling ----------
    def _is_remote_panel_stale(self, panel: dict) -> bool:
        if not panel:
            return True
        try:
            updated_at = float(panel.get("updated_at", 0) or 0)
        except Exception:
            return True
        if updated_at <= 0:
            return True
        return (time.time() - updated_at) > float(REMOTE_PANEL_STALE_SECONDS)

    def _remote_panel_prune_tick(self, _dt):
        self._prune_stale_remote_panels()

    def _apply_remote_panel_message(self, msg: dict, addr):
        result = super()._apply_remote_panel_message(msg, addr)
        try:
            if int(getattr(base, "PANEL_POSITION", 0)) == 0 and self.backend_client:
                self.backend_sync_items_async()
        except Exception:
            pass
        return result

    def _prune_stale_remote_panels(self):
        if int(getattr(base, "PANEL_POSITION", 0)) != 0:
            return False

        removed_positions = []
        for pos, panel in list(getattr(self, "remote_panels", {}).items()):
            try:
                pos_i = int(pos)
            except Exception:
                continue
            if pos_i == int(getattr(base, "PANEL_POSITION", 0)):
                continue
            if self._is_remote_panel_stale(panel):
                removed_positions.append(pos_i)
                self.remote_panels.pop(pos_i, None)

        if not removed_positions:
            return False

        try:
            base.save_remote_panels_state(self.remote_panels)
        except Exception:
            pass

        if int(getattr(self, "current_panel_position", 0)) in removed_positions:
            self.current_panel_position = int(getattr(base, "PANEL_POSITION", 0))
            try:
                self._sync_current_panel_layout()
            except Exception:
                pass
            if getattr(self, "current_item_position", None) in removed_positions:
                self.current_item_position = int(getattr(base, "PANEL_POSITION", 0))
                self.current_item_id = None

        try:
            self._refresh_panel_nav_buttons()
            self.refresh_selection_grid()
            self.refresh_check_price_grid()
            self.refresh_admin_grid()
            self.refresh_cart_screen()
            self._update_item_detail_screen()
        except Exception:
            pass

        print(f"[REMOTE] Pruned stale side panels: {removed_positions}")
        try:
            if self.backend_client:
                self.backend_sync_items_async()
        except Exception:
            pass
        return True

    def available_panel_positions(self):
        if int(getattr(base, "PANEL_POSITION", 0)) != 0:
            return [int(getattr(base, "PANEL_POSITION", 0))]

        self._prune_stale_remote_panels()

        alive = [int(getattr(base, "PANEL_POSITION", 0))]
        for pos, panel in getattr(self, "remote_panels", {}).items():
            try:
                pos_i = int(pos)
            except Exception:
                continue
            if pos_i == int(getattr(base, "PANEL_POSITION", 0)):
                continue
            if not self._is_remote_panel_stale(panel):
                alive.append(pos_i)

        return sorted(alive)

    def panel_items_for_position(self, position: int):
        position = int(position)
        if position == int(getattr(base, "PANEL_POSITION", 0)):
            return self.items

        panel = getattr(self, "remote_panels", {}).get(position)
        if not panel or self._is_remote_panel_stale(panel):
            return []
        return panel.get("items", [])

    def switch_panel_view(self, direction: int):
        positions = self.available_panel_positions()
        current = int(getattr(self, "current_panel_position", 0))
        idx = positions.index(current) if current in positions else 0
        nxt = idx + (1 if direction > 0 else -1)
        if not (0 <= nxt < len(positions)):
            return

        # Clear old widgets before changing grid row counts between panel types
        for screen_name, left_id, right_id in (
            ("selection", "items_grid_left", "items_grid_right"),
            ("price_check", "check_grid_left", "check_grid_right"),
            ("admin_panel", "admin_grid_left", "admin_grid_right"),
        ):
            try:
                scr = self.root.get_screen(screen_name)
                scr.ids[left_id].clear_widgets()
                scr.ids[right_id].clear_widgets()
            except Exception:
                pass

        self.current_panel_position = int(positions[nxt])
        self._sync_current_panel_layout()
        self.refresh_selection_grid()
        self.refresh_check_price_grid()
        self.refresh_admin_grid()

    # ---------- component status ----------
    def _probe_tcp_service(self, host: str, port: int, timeout: float = COMPONENT_PROBE_TIMEOUT_S):
        try:
            sock = socket.create_connection((str(host), int(port)), timeout=float(timeout))
            sock.close()
            return True, ""
        except socket.timeout:
            return False, self.t("relay_timeout")
        except TimeoutError:
            return False, self.t("relay_timeout")
        except OSError as e:
            msg = str(e).lower()
            if "timed out" in msg or "timeout" in msg:
                return False, self.t("relay_timeout")
            return False, self.t("relay_not_ready")
        except Exception:
            return False, self.t("relay_not_ready")

    def _collect_component_statuses(self) -> dict[str, Any]:
        statuses: dict[str, Any] = {}

        payment_error = str(getattr(self, "payment_start_error", "") or "")
        if bool(getattr(self, "payment_ready", False)):
            payment_status = "ok"
        elif payment_error:
            payment_status = "error"
        else:
            payment_status = "starting"
        statuses["payment_terminal"] = {
            "status": payment_status,
            "last_error": payment_error or None,
        }

        try:
            relay_cfgs_func = getattr(base, "relay_configs_for_item_ids", None)
            if callable(relay_cfgs_func):
                relay_cfgs = list(relay_cfgs_func(None) or [])
            else:
                relay_cfgs = [getattr(base, "RELAY_CFG", None)]
        except Exception:
            relay_cfgs = [getattr(base, "RELAY_CFG", None)]

        relay_entries = []
        relay_errors = []
        for relay_cfg in relay_cfgs:
            relay_host = getattr(relay_cfg, "host", None)
            relay_port = getattr(relay_cfg, "port", None)
            if not relay_host or not relay_port:
                continue
            relay_ok, relay_error = self._probe_tcp_service(relay_host, relay_port, timeout=COMPONENT_PROBE_TIMEOUT_S)
            try:
                relay_name = base.relay_label_for_cfg(relay_cfg)
            except Exception:
                relay_name = str(relay_host)
            relay_entries.append({
                "name": relay_name,
                "status": "ok" if relay_ok else "error",
                "host": str(relay_host),
                "port": int(relay_port),
                "last_error": relay_error or None,
            })
            if not relay_ok:
                relay_errors.append(f"{relay_name}: {relay_error or 'not ready'}")

        if relay_entries:
            statuses["relay"] = {
                "status": "ok" if not relay_errors else "error",
                "host": ",".join(str(x.get("host")) for x in relay_entries),
                "port": int(relay_entries[0].get("port") or 502),
                "relays": relay_entries,
                "last_error": "; ".join(relay_errors) if relay_errors else None,
            }
        else:
            statuses["relay"] = {
                "status": "unknown",
                "last_error": None,
            }

        statuses["fiscalisation"] = {
            "status": str(self._last_fiscal_status or "unknown"),
            "last_error": (self._last_fiscal_error or None),
        }

        statuses["remote_panels"] = {
            "status": "ok",
            "count": int(len(getattr(self, "remote_panels", {}) or {})),
            "positions": sorted([int(x) for x in (getattr(self, "remote_panels", {}) or {}).keys()]),
        }

        return statuses

    def _reservation_release_async(self, *, code: str, claim_token: str, reason: str = "relay_failed"):
        def worker():
            try:
                self.reservation_client.release(
                    code=code,
                    claim_token=claim_token,
                    panel_id=BACKEND_PANEL_ID,
                    released_by="panel",
                    release_reason=reason,
                )
                Clock.schedule_once(lambda _dt, c=code: self._set_local_reservation_status(c, "active"), 0)
            except Exception as e:
                print(f"[RESERVATION] release failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _reservation_confirm_async(self, *, code: str, claim_token: str, on_ok=None, on_fail=None):
        def worker():
            try:
                result = self.reservation_client.confirm(
                    code=code,
                    claim_token=claim_token,
                    panel_id=BACKEND_PANEL_ID,
                    redeemed_by="panel",
                )
                if bool((result or {}).get("confirmed")):
                    if on_ok:
                        Clock.schedule_once(lambda _dt, r=result: on_ok(r), 0)
                else:
                    if on_fail:
                        Clock.schedule_once(lambda _dt, r=result: on_fail(r), 0)
            except Exception as e:
                if on_fail:
                    Clock.schedule_once(lambda _dt, msg=str(e): on_fail({"ok": False, "reason": msg}), 0)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- backend sync ----------
    def _start_backend_threads(self):
        if not self.backend_client:
            return

        def fetch_and_apply_config(force_register: bool = False) -> bool:
            try:
                if force_register:
                    try:
                        self.backend_client.register()
                    except Exception:
                        pass
                cfg = self.backend_client.get_config()
                self.backend_ready = True
                self.backend_last_error = ""
                Clock.schedule_once(lambda _dt, c=cfg: self._backend_apply_config(c), 0)
                return True
            except Exception as e:
                self.backend_ready = False
                self.backend_last_error = str(e)
                return False

        def startup_worker():
            posted_startup_event = False
            while not self._backend_stop:
                try:
                    self.backend_client.register()
                    self.backend_client.sync_items(self._backend_visible_items_snapshot(), replace_missing=True)
                    self._maybe_sync_admin_users(force=True)
                    cfg = self.backend_client.get_config()
                    self.backend_ready = True
                    self.backend_last_error = ""
                    Clock.schedule_once(lambda _dt, c=cfg: self._backend_apply_config(c), 0)
                    if not posted_startup_event:
                        self.backend_post_event_async(
                            "panel_startup",
                            payload={
                                "panel_type": self.panel_type,
                                "position": int(getattr(base, "PANEL_POSITION", 0)),
                                "payment_ready": bool(self.payment_ready),
                            },
                        )
                        posted_startup_event = True
                    break
                except Exception as e:
                    self.backend_ready = False
                    self.backend_last_error = str(e)
                    print(f"[BACKEND] startup failed: {e}")
                    time.sleep(BACKEND_STARTUP_RETRY_SECONDS)

        def heartbeat_loop():
            while not self._backend_stop:
                try:
                    if self.backend_client:
                        current_screen = str(getattr(self.root, "current", "") or "") if self.root else ""
                        self.backend_client.heartbeat(
                            status="online",
                            current_screen=current_screen,
                            cart_count=len(self.cart_items()),
                            payment_terminal_ready=bool(self.payment_ready),
                            component_statuses=self._collect_component_statuses(),
                        )
                        self.backend_ready = True
                        self.backend_last_error = ""
                        self._maybe_sync_admin_users()
                except Exception as e:
                    self.backend_ready = False
                    self.backend_last_error = str(e)
                    print(f"[BACKEND] heartbeat failed: {e}")
                time.sleep(BACKEND_HEARTBEAT_SECONDS)

        def command_loop():
            while not self._backend_stop:
                try:
                    if self.backend_client:
                        data = self.backend_client.poll_commands()
                        commands = data.get("commands") or []
                        ack_versions: list[int] = []
                        for cmd in commands:
                            version = int(cmd.get("version", 0) or 0)
                            self._backend_apply_command(cmd)
                            if version > 0:
                                ack_versions.append(version)
                        if ack_versions:
                            self.backend_client.ack_commands(ack_versions)
                except Exception as e:
                    self.backend_ready = False
                    self.backend_last_error = str(e)
                    print(f"[BACKEND] command poll failed: {e}")
                time.sleep(BACKEND_COMMAND_POLL_SECONDS)

        def config_loop():
            while not self._backend_stop:
                try:
                    if self.backend_client:
                        ok = fetch_and_apply_config(force_register=(not bool(self.backend_ready)))
                        if not ok and not self.backend_ready:
                            try:
                                self.backend_client.register()
                                self.backend_client.sync_items(self._backend_visible_items_snapshot(), replace_missing=True)
                                self._maybe_sync_admin_users(force=True)
                            except Exception:
                                pass
                except Exception as e:
                    self.backend_ready = False
                    self.backend_last_error = str(e)
                    print(f"[BACKEND] config refresh failed: {e}")
                time.sleep(BACKEND_CONFIG_REFRESH_SECONDS)

        threading.Thread(target=startup_worker, daemon=True).start()
        threading.Thread(target=heartbeat_loop, daemon=True).start()
        threading.Thread(target=command_loop, daemon=True).start()
        threading.Thread(target=config_loop, daemon=True).start()

    def _backend_upsert_item_local(self, item_update: dict) -> bool:
        try:
            item_id = int(item_update.get("item_id", item_update.get("id")))
        except Exception:
            return False

        it = self._get_panel_item(int(getattr(base, "PANEL_POSITION", 0)), item_id)
        if it is None:
            return False

        if item_update.get("label") is not None:
            it["label"] = str(item_update.get("label"))
        if item_update.get("price") is not None:
            it["price"] = float(item_update.get("price"))
        if item_update.get("tax_pct") is not None:
            it["tax_pct"] = float(item_update.get("tax_pct"))
        if item_update.get("enabled") is not None:
            it["enabled"] = bool(item_update.get("enabled"))

        base.save_items_state(self.items)
        return True

    def _backend_upsert_item_remote(self, panel_position: int, item_update: dict) -> bool:
        payload = {
            "type": "remote_update_item",
            "item": {
                "id": int(item_update.get("item_id", item_update.get("id"))),
                "label": str(item_update.get("label")),
                "price": float(item_update.get("price")),
                "tax_pct": float(item_update.get("tax_pct")),
                "enabled": bool(item_update.get("enabled")),
            },
        }
        ack = self._send_remote_message_with_ack(int(panel_position), payload)
        if not ack.get("ok"):
            print(f"[BACKEND] remote side item upsert failed: {ack}")
            return False

        remote_item = self._get_panel_item(int(panel_position), int(item_update.get("item_id", item_update.get("id"))))
        if remote_item is not None:
            remote_item.update({
                "label": payload["item"]["label"],
                "price": payload["item"]["price"],
                "tax_pct": payload["item"]["tax_pct"],
                "enabled": payload["item"]["enabled"],
            })
        panel = getattr(self, "remote_panels", {}).get(int(panel_position))
        if panel is not None:
            panel["updated_at"] = time.time()
            try:
                if int(getattr(base, "PANEL_POSITION", 0)) == 0:
                    base.save_remote_panels_state(self.remote_panels)
            except Exception:
                pass
        return True

    def _backend_apply_side_item_upsert(self, payload: dict):
        try:
            panel_position = int(payload.get("panel_position", getattr(base, "PANEL_POSITION", 0)))
            item_update = {
                "item_id": int(payload.get("item_id", payload.get("id"))),
                "label": payload.get("label"),
                "price": payload.get("price"),
                "tax_pct": payload.get("tax_pct"),
                "enabled": payload.get("enabled"),
            }
        except Exception as e:
            print(f"[BACKEND] bad side_item_upsert payload: {payload} error={e}")
            return

        ok = False
        if panel_position == int(getattr(base, "PANEL_POSITION", 0)):
            ok = self._backend_upsert_item_local(item_update)
        else:
            ok = self._backend_upsert_item_remote(panel_position, item_update)

        if ok:
            try:
                self.refresh_selection_grid()
                self.refresh_check_price_grid()
                self.refresh_admin_grid()
                self.refresh_cart_screen()
                self._update_item_detail_screen()
            except Exception:
                pass
            try:
                if self.backend_client:
                    self.backend_sync_items_async()
            except Exception:
                pass
            self.backend_post_event_async(
                "side_item_upsert_applied",
                payload={"panel_position": panel_position, "item": item_update},
            )
        else:
            self.backend_post_event_async(
                "side_item_upsert_failed",
                severity="warning",
                payload={"panel_position": panel_position, "item": item_update},
            )

    def _backend_apply_command(self, cmd: dict):
        command_type = str(cmd.get("command_type", "") or "")
        payload = cmd.get("payload") or {}
        print(f"[BACKEND] command: {command_type} payload={payload}")

        if command_type in ("config_refresh", "reservation_refresh"):
            try:
                cfg = self.backend_client.get_config()
                Clock.schedule_once(lambda _dt, c=cfg: self._backend_apply_config(c), 0)
            except Exception as e:
                print(f"[BACKEND] config refresh failed: {e}")
        elif command_type == "reopen_door":
            item_ids = [int(x) for x in (payload.get("item_ids") or []) if x is not None]
            panel_position = int(payload.get("panel_position", base.PANEL_POSITION) or base.PANEL_POSITION)
            if panel_position == int(base.PANEL_POSITION):
                self.open_doors_sequential(item_ids, pulse_ms=base.DOOR_PULSE_MS, gap_ms=base.DOOR_GAP_MS)
            else:
                try:
                    ack = self._send_remote_message_with_ack(panel_position, {
                        "type": "remote_reopen",
                        "items": item_ids,
                    })
                    print(f"[BACKEND] reopen_door remote ack={ack}")
                except Exception as e:
                    print(f"[BACKEND] reopen_door remote failed: {e}")
        elif command_type in ("side_item_upsert", "remote_item_upsert"):
            Clock.schedule_once(lambda _dt, p=deepcopy(payload): self._backend_apply_side_item_upsert(p), 0)
        elif command_type == "side_panel_refresh":
            try:
                panel_position = int(payload.get("panel_position"))
                ack = self._send_remote_message_with_ack(panel_position, {"type": "remote_request_state"})
                print(f"[BACKEND] side_panel_refresh ack={ack}")
            except Exception as e:
                print(f"[BACKEND] side_panel_refresh failed: {e}")

    def _backend_apply_config(self, cfg: dict):
        if not cfg:
            return
        self.backend_config_version = int(cfg.get("config_version", self.backend_config_version) or self.backend_config_version)
        self.server_reservation_codes = list(cfg.get("reservation_codes") or [])

        server_items = cfg.get("items") or []
        if server_items:
            self._backend_applying_config = True
            try:
                merged_items = [deepcopy(it) for it in getattr(self, "items", [])]
                if not merged_items and hasattr(base, "_default_items"):
                    try:
                        merged_items = [deepcopy(it) for it in base._default_items()]
                    except Exception:
                        merged_items = []
                normalized = [
                    {
                        "id": int(it["item_id"]),
                        "label": str(it.get("label", it["item_id"])),
                        "price": float(it.get("price", 0.0)),
                        "tax_pct": float(it.get("tax_pct", 0.0)),
                        "enabled": bool(it.get("enabled", True)),
                    }
                    for it in server_items
                ]
                for idx, item in enumerate(normalized):
                    if idx < len(merged_items):
                        merged_items[idx].update(item)
                    else:
                        merged_items.append(item)
                self.items = merged_items if merged_items else normalized
                base.save_items_state(self.items)
            finally:
                self._backend_applying_config = False

        self.refresh_selection_grid()
        self.refresh_check_price_grid()
        self.refresh_admin_grid()
        self.refresh_cart_screen()
        if self.current_item_id is not None:
            self._update_item_detail_screen()

    def backend_sync_items_async(self):
        if not self.backend_client:
            return
        items_snapshot = self._backend_visible_items_snapshot()

        def worker():
            try:
                self.backend_client.sync_items(items_snapshot, replace_missing=True)
            except Exception as e:
                print(f"[BACKEND] item sync failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def backend_post_event_async(self, event_type: str, *, severity: str = "info", payload: dict | None = None):
        if not self.backend_client:
            return

        def worker():
            try:
                self.backend_client.post_event(event_type, severity=severity, payload=payload or {})
            except Exception as e:
                print(f"[BACKEND] event post failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def backend_post_sale_async(
        self,
        invoice_number: str,
        issue_datetime_iso: str,
        total: float,
        items: list,
        payment_status: str,
        fiscal_status: str,
    ):
        if not self.backend_client:
            return
        items_snapshot = [deepcopy(it) for it in (items or [])]

        def worker():
            try:
                self.backend_client.post_sale(
                    invoice_number=invoice_number,
                    issue_datetime_iso=issue_datetime_iso,
                    total_amount=total,
                    payment_status=payment_status,
                    fiscal_status=fiscal_status,
                    items=items_snapshot,
                )
            except Exception as e:
                print(f"[BACKEND] sale post failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def backend_post_door_event_async(
        self,
        item_id: int,
        panel_position: int,
        action: str,
        success: bool,
        error_text: str | None = None,
    ):
        if not self.backend_client:
            return

        def worker():
            try:
                self.backend_client.post_door_event(
                    item_id=item_id,
                    panel_position=panel_position,
                    action=action,
                    success=success,
                    error_text=error_text,
                )
            except Exception as e:
                print(f"[BACKEND] door event failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    # ---------- translations / reservation UI ----------
    def apply_translations(self):
        try:
            super().apply_translations()
        except Exception:
            pass
        try:
            _backend_force_white_keyboard_widgets(self)
        except Exception:
            pass
        try:
            rs = self.root.get_screen("reservation")
            rs.ids.reservation_title.text = self.t("reservation_title")
            rs.ids.reservation_code_input.hint_text = self.t("reservation_enter_code")
            rs.ids.reservation_collect_btn.text = self.t("reservation_collect")
            rs.ids.reservation_back_btn.text = self.t("back")
            rs.ids.reservation_home_btn.text = self.t("home")
            if self._verified_reservation:
                panel_position = int(self._verified_reservation.get("panel_position", 0))
                label = self._verified_reservation.get("label") or self._verified_reservation.get("code") or ""
                item_id = self._verified_reservation.get("item_id")
                rs.ids.reservation_status.text = f"{self.t('reservation_ready')}\n{label} | {item_id} | {panel_position}"
            elif rs.ids.reservation_status.text == self.t("reservation_body") or "to be implemented" in rs.ids.reservation_status.text.lower():
                rs.ids.reservation_status.text = ""
            rs.ids.reservation_collect_btn.disabled = False
            self._force_reservation_status_white()
        except Exception:
            pass

    def goto_reservation(self):
        self._cancel_timers()
        self._verified_reservation = None
        self.active_textinput = None
        self.keyboard_upper = False
        try:
            rs = self.root.get_screen("reservation")
            rs.ids.reservation_status.text = ""
            rs.ids.reservation_collect_btn.disabled = False
            rs.ids.reservation_code_input.text = ""
        except Exception:
            pass
        self.root.current = "reservation"
        try:
            self._force_light_input_text()
        except Exception:
            pass
        try:
            self._force_reservation_status_white()
        except Exception:
            pass
        try:
            Clock.schedule_once(lambda _dt: setattr(self.root.get_screen("reservation").ids.reservation_code_input, "focus", True), 0.1)
        except Exception:
            pass

    def reservation_clear(self):
        self._verified_reservation = None
        try:
            rs = self.root.get_screen("reservation")
            rs.ids.reservation_code_input.text = ""
            rs.ids.reservation_status.text = ""
            rs.ids.reservation_collect_btn.disabled = False
            self.active_textinput = rs.ids.reservation_code_input
            try:
                self._force_light_input_text()
            except Exception:
                pass
            Clock.schedule_once(lambda _dt: setattr(rs.ids.reservation_code_input, "focus", True), 0)
            Clock.schedule_once(lambda _dt: self._force_light_input_text(), 0.05)
        except Exception:
            pass

    def _reservation_reason_text(self, reason: str) -> str:
        reason = str(reason or "").lower()
        if reason == "expired":
            return self.t("reservation_code_expired")
        if reason in ("status_redeemed", "status_used"):
            return self.t("reservation_code_redeemed")
        if reason == "status_claimed":
            return self.t("reservation_code_claimed")
        if reason == "code_not_found":
            return self.t("reservation_code_invalid")
        if reason in ("claim_token_invalid", "claim_expired"):
            return self.t("reservation_backend_error")
        return self.t("reservation_backend_error")

    def _reservation_verify_then_collect_invalid(self, result: dict):
        rs = self.root.get_screen("reservation")
        rs.ids.reservation_status.text = self._reservation_reason_text((result or {}).get("reason"))
        rs.ids.reservation_collect_btn.disabled = False
        self._verified_reservation = None
        self.backend_post_event_async(
            "reservation_verify_failed",
            severity="warning",
            payload={"result": result or {}},
        )

    def reservation_collect(self):
        rs = self.root.get_screen("reservation")
        code = rs.ids.reservation_code_input.text.strip()
        if not code:
            rs.ids.reservation_status.text = self.t("reservation_code_invalid")
            rs.ids.reservation_collect_btn.disabled = False
            self._verified_reservation = None
            self._reservation_claim_token = None
            return

        rs.ids.reservation_status.text = self.t("reservation_collecting")
        rs.ids.reservation_collect_btn.disabled = True
        self._verified_reservation = None
        self._reservation_claim_token = None

        panel_id = BACKEND_PANEL_ID

        def worker():
            try:
                claim_result = self.reservation_client.claim(
                    code=code,
                    panel_id=panel_id,
                    claimed_by="panel",
                    hold_seconds=RESERVATION_HOLD_SECONDS,
                )
            except Exception as e:
                Clock.schedule_once(
                    lambda _dt, msg=str(e): self._reservation_collect_failed(msg),
                    0,
                )
                return

            if not bool((claim_result or {}).get("claimed")):
                Clock.schedule_once(lambda _dt, r=claim_result: self._reservation_verify_then_collect_invalid(r), 0)
                return

            reservation = (claim_result or {}).get("reservation") or {}
            claim_token = str((claim_result or {}).get("claim_token") or "")
            Clock.schedule_once(lambda _dt, r=reservation, c=code, t=claim_token: self._reservation_collect_claimed(c, t, r), 0)

        threading.Thread(target=worker, daemon=True).start()

    def _reservation_collect_failed(self, message: str):
        rs = self.root.get_screen("reservation")
        rs.ids.reservation_status.text = self.t('reservation_backend_error')
        rs.ids.reservation_collect_btn.disabled = False
        self._reservation_claim_token = None
        self.backend_post_event_async(
            "reservation_collect_failed",
            severity="warning",
            payload={"error": message},
        )

    def _reservation_collect_claimed(self, code: str, claim_token: str, reservation: dict):
        rs = self.root.get_screen("reservation")
        self._verified_reservation = reservation or {}
        self._reservation_claim_token = claim_token or None

        panel_position = int((reservation or {}).get("panel_position", 0))
        item_id = (reservation or {}).get("item_id")
        label = (reservation or {}).get("label") or (reservation or {}).get("code") or code

        if item_id is None or not claim_token:
            rs.ids.reservation_status.text = self.t("reservation_backend_error")
            rs.ids.reservation_collect_btn.disabled = False
            if claim_token:
                self._reservation_release_async(code=code, claim_token=claim_token, reason="missing_item")
            return

        self.backend_post_event_async(
            "reservation_claimed",
            payload={"reservation": reservation, "claim_token": claim_token},
        )

        if int(panel_position) == int(getattr(base, "PANEL_POSITION", 0)):
            self.open_doors_sequential(
                [int(item_id)],
                pulse_ms=base.DOOR_PULSE_MS,
                gap_ms=base.DOOR_GAP_MS,
                on_complete=lambda results, iid=int(item_id), lbl=str(label), p=int(panel_position), c=code, t=claim_token: self._reservation_local_open_done(iid, lbl, p, c, t, results),
            )
        else:
            ack = self._send_remote_message_with_ack(
                int(panel_position),
                {
                    "type": "remote_purchase",
                    "items": [int(item_id)],
                    "reservation_code": code,
                },
            )
            results = ack.get("results") or [{"id": int(item_id), "ok": bool(ack.get("ok")), "error": ack.get("error")}]
            self._reservation_remote_open_done(int(item_id), str(label), int(panel_position), code, claim_token, results)

    def _reservation_collect_confirm_failed(self, code: str, claim_token: str, result: dict):
        rs = self.root.get_screen("reservation")
        rs.ids.reservation_status.text = self.t("reservation_backend_error")
        rs.ids.reservation_collect_btn.disabled = False
        self.backend_post_event_async(
            "reservation_confirm_failed",
            severity="warning",
            payload={"code": code, "result": result or {}},
        )

    def _reservation_local_open_done(self, item_id: int, label: str, panel_position: int, code: str, claim_token: str, results: list[dict]):
        rs = self.root.get_screen("reservation")
        ok = any(bool(r.get("ok")) and int(r.get("id")) == int(item_id) for r in (results or []))
        if not ok:
            error_text = next((str(r.get("error")) for r in (results or []) if int(r.get("id", -1)) == int(item_id) and r.get("error")), "")
            self._reservation_release_async(code=code, claim_token=claim_token, reason="relay_failed")
            rs.ids.reservation_status.text = self.t('door_open_failed')
            rs.ids.reservation_collect_btn.disabled = False
            return

        def on_confirm_ok(_result):
            it = self._get_panel_item(panel_position, item_id)
            if it is not None:
                it["enabled"] = False
                base.save_items_state(self.items)
            self.refresh_selection_grid()
            self.refresh_check_price_grid()
            self.refresh_admin_grid()
            self.refresh_cart_screen()
            if self.current_item_id is not None:
                self._update_item_detail_screen()

            self._last_purchased_items = [{"id": int(item_id), "label": str(label), "price": None, "panel_position": int(panel_position)}]
            try:
                if self.backend_client:
                    self.backend_sync_items_async()
            except Exception:
                pass
            self.receipt_available = False
            self.apply_translations()
            rs.ids.reservation_status.text = self.t("reservation_success")
            self._set_local_reservation_status(code, "redeemed")
            self._verified_reservation = None
            self._reservation_claim_token = None
            Clock.schedule_once(lambda _dt: self._show_reservation_success_screen(), 0.2)

        self._reservation_confirm_async(
            code=code,
            claim_token=claim_token,
            on_ok=on_confirm_ok,
            on_fail=lambda result, c=code, t=claim_token: self._reservation_collect_confirm_failed(c, t, result),
        )

    def _reservation_remote_open_done(self, item_id: int, label: str, panel_position: int, code: str, claim_token: str, results: list[dict]):
        rs = self.root.get_screen("reservation")
        ok = any(bool(r.get("ok")) and int(r.get("id")) == int(item_id) for r in (results or []))
        if not ok:
            error_text = next((str(r.get("error")) for r in (results or []) if int(r.get("id", -1)) == int(item_id) and r.get("error")), "")
            self._reservation_release_async(code=code, claim_token=claim_token, reason="remote_open_failed")
            rs.ids.reservation_status.text = self.t('door_open_failed')
            rs.ids.reservation_collect_btn.disabled = False
            return

        def on_confirm_ok(_result):
            panel = self.remote_panels.get(int(panel_position))
            if panel is not None:
                for remote_item in (panel.get("items") or []):
                    if int(remote_item.get("id")) == int(item_id):
                        remote_item["enabled"] = False
                        break
            self.refresh_selection_grid()
            self.refresh_check_price_grid()
            self.refresh_admin_grid()
            self.refresh_cart_screen()
            if self.current_item_id is not None:
                self._update_item_detail_screen()

            self._last_purchased_items = [{"id": int(item_id), "label": str(label), "price": None, "panel_position": int(panel_position)}]
            try:
                if self.backend_client:
                    self.backend_sync_items_async()
            except Exception:
                pass
            self.receipt_available = False
            self.apply_translations()
            rs.ids.reservation_status.text = self.t("reservation_success")
            self._set_local_reservation_status(code, "redeemed")
            self._verified_reservation = None
            self._reservation_claim_token = None
            Clock.schedule_once(lambda _dt: self._show_reservation_success_screen(), 0.2)

        self._reservation_confirm_async(
            code=code,
            claim_token=claim_token,
            on_ok=on_confirm_ok,
            on_fail=lambda result, c=code, t=claim_token: self._reservation_collect_confirm_failed(c, t, result),
        )

    def _show_reservation_success_screen(self):
        self._update_thanks_opening_label()
        try:
            thx = self.root.get_screen("thanks")
            thx.ids.thanks_sub.color = (1, 1, 1, 0.98)
        except Exception:
            pass
        self.root.current = "thanks"
        self._cancel_timers()
        self._thanks_event = Clock.schedule_once(lambda _dt: self.goto_menu(), base.THANKYOU_SECONDS)

    # ---------- overrides for event reporting ----------
    def admin_login(self):
        before_user = self.current_admin_username
        before_role = self.current_admin_role
        super().admin_login()
        if self.current_admin_username and (self.current_admin_username != before_user or self.current_admin_role != before_role):
            self.backend_post_event_async(
                "admin_login",
                payload={
                    "username": self.current_admin_username,
                    "role": self.current_admin_role,
                    "screen": getattr(self.root, "current", ""),
                },
            )

    def goto_menu(self):
        logout_user = None
        logout_role = None
        try:
            if self.root and self.root.current in ("admin_login", "admin_panel", "admin_edit_item") and self.current_admin_username:
                logout_user = self.current_admin_username
                logout_role = self.current_admin_role
        except Exception:
            pass
        super().goto_menu()
        if logout_user:
            self.backend_post_event_async(
                "admin_logout",
                payload={"username": logout_user, "role": logout_role},
            )

    def open_doors_sequential(self, hidden_ids, action: str = base.DOOR_ACTION, pulse_ms: int = base.DOOR_PULSE_MS, gap_ms: int = base.DOOR_GAP_MS, on_complete=None):
        def wrapped(results):
            try:
                for result in (results or []):
                    item_id = int(result.get("id"))
                    self.backend_post_door_event_async(
                        item_id=item_id,
                        panel_position=int(getattr(base, "PANEL_POSITION", 0)),
                        action=str(action),
                        success=bool(result.get("ok")),
                        error_text=result.get("error"),
                    )
            except Exception as e:
                print(f"[BACKEND] local door result logging failed: {e}")
            if on_complete:
                on_complete(results)

        return super().open_doors_sequential(hidden_ids, action=action, pulse_ms=pulse_ms, gap_ms=gap_ms, on_complete=wrapped)

    def _handle_payment_failure(self, items, error_text: str):
        self.backend_post_event_async(
            "payment_failed",
            severity="warning",
            payload={
                "error": str(error_text or ""),
                "items": items or [],
            },
        )
        super()._handle_payment_failure(items, error_text)

    def _handle_payment_result(self, items, invoice_number: str, issue_datetime_iso: str, total: float, result: dict):
        self.backend_post_event_async(
            "payment_result",
            payload={
                "invoice_number": invoice_number,
                "issue_datetime_iso": issue_datetime_iso,
                "total": float(total),
                "result": result or {},
            },
        )
        super()._handle_payment_result(items, invoice_number, issue_datetime_iso, total, result)

    def _complete_successful_purchase(self, items, invoice_number: str, issue_datetime_iso: str, total: float):
        self._last_invoice_number = str(invoice_number)
        self._last_issue_datetime_iso = str(issue_datetime_iso)
        try:
            self._last_purchase_total = float(total)
        except Exception:
            self._last_purchase_total = 0.0
        self._backend_pending_sales[invoice_number] = {
            "items": [deepcopy(it) for it in (items or [])],
            "total": float(total),
        }
        self.backend_post_event_async(
            "purchase_completed",
            payload={
                "invoice_number": invoice_number,
                "issue_datetime_iso": issue_datetime_iso,
                "total": float(total),
                "items": items or [],
            },
        )
        super()._complete_successful_purchase(items, invoice_number, issue_datetime_iso, total)

    def _worker_send(self, invoice_number: str, issue_datetime_iso: str, *args, **kwargs):
        ctx = self._backend_pending_sales.get(invoice_number) or {}
        items = ctx.get("items") or kwargs.get("items") or []
        total = float(ctx.get("total", 0.0))
        if not total and items:
            total = sum(float(it.get("price", 0.0)) for it in items)

        fiscal_status = "error"
        result = None
        self._last_fiscal_error = ""

        try:
            sig = inspect.signature(base.process_transaction)
            if "items" in sig.parameters:
                result = base.process_transaction(
                    invoice_number=invoice_number,
                    issue_datetime_iso=issue_datetime_iso,
                    items=items,
                )
            elif "amount" in sig.parameters:
                amount = kwargs.get("amount")
                if amount is None and args:
                    amount = args[0]
                if amount is None:
                    amount = total
                result = base.process_transaction(
                    invoice_number=invoice_number,
                    issue_datetime_iso=issue_datetime_iso,
                    amount=float(amount),
                )
            else:
                result = base.process_transaction(invoice_number=invoice_number, issue_datetime_iso=issue_datetime_iso)

            if isinstance(result, dict):
                if result.get("inv_ok"):
                    fiscal_status = "ok"
                elif result.get("queued_failed"):
                    fiscal_status = "queued_failed"
                elif result.get("skipped"):
                    fiscal_status = "skipped"
                else:
                    fiscal_status = "failed"
                    self._last_fiscal_error = str(result)
            else:
                fiscal_status = "ok"
        except Exception as e:
            fiscal_status = "error"
            result = {"exception": str(e)}
            self._last_fiscal_error = str(e)
        finally:
            self._last_fiscal_status = fiscal_status
            self._fiscal_results_by_invoice[str(invoice_number)] = {
                "status": fiscal_status,
                "result": result or {},
                "updated_at": time.time(),
            }
            if not total and items:
                total = sum(float(it.get("price", 0.0)) for it in items)
            append_monthly_invoice_csv(
                invoice_number=invoice_number,
                issue_datetime_iso=issue_datetime_iso,
                total=total,
                items=items,
            )
            self.backend_post_sale_async(
                invoice_number=invoice_number,
                issue_datetime_iso=issue_datetime_iso,
                total=total,
                items=items,
                payment_status="paid",
                fiscal_status=fiscal_status,
            )
            self.backend_post_event_async(
                "fiscalisation_result",
                payload={
                    "invoice_number": invoice_number,
                    "issue_datetime_iso": issue_datetime_iso,
                    "total": total,
                    "fiscal_status": fiscal_status,
                    "result": result or {},
                },
            )
            self._backend_pending_sales.pop(invoice_number, None)


    def _receipt_printer_config(self):
        if ReceiptPrinterConfig is None:
            return None
        return ReceiptPrinterConfig(
            enabled=bool(RECEIPT_PRINTER_ENABLED),
            connection=str(RECEIPT_PRINTER_CONNECTION),
            device=str(RECEIPT_PRINTER_DEVICE),
            serial_baudrate=int(RECEIPT_PRINTER_SERIAL_BAUDRATE),
            tax_number=str(RECEIPT_PRINTER_TAX_NUMBER),
            business_name=str(RECEIPT_PRINTER_BUSINESS_NAME),
            operator_name=str(RECEIPT_PRINTER_OPERATOR_NAME),
            address_line=str(RECEIPT_PRINTER_ADDRESS_LINE),
            qr_module_size=int(RECEIPT_PRINTER_QR_MODULE_SIZE),
            cut_paper=bool(RECEIPT_PRINTER_CUT_PAPER),
        )

    def _set_receipt_print_status(self, message: str):
        def _update(_dt):
            try:
                rp = self.root.get_screen("receipt_printing")
                base_text = str(rp.ids.receipt_print_for.text or "").split("\n\n[", 1)[0]
                rp.ids.receipt_print_for.text = base_text + "\n\n[" + str(message) + "]"
            except Exception:
                pass
        try:
            Clock.schedule_once(_update, 0)
        except Exception:
            print(f"[RECEIPT PRINTER] {message}")

    def _wait_for_fiscal_result(self, invoice_number: str) -> dict[str, Any]:
        deadline = time.time() + max(0.0, float(RECEIPT_PRINTER_WAIT_FOR_FISCAL_SECONDS))
        while time.time() < deadline:
            info = self._fiscal_results_by_invoice.get(str(invoice_number))
            if info is not None:
                return info
            time.sleep(0.15)
        return self._fiscal_results_by_invoice.get(str(invoice_number), {"status": "pending", "result": {}})

    def _build_last_receipt_data(self, invoice_number: str, fiscal_info: dict[str, Any]) -> dict[str, Any]:
        items = list(getattr(self, "_last_purchased_items", []) or [])
        total = float(getattr(self, "_last_purchase_total", 0.0) or 0.0)
        if not total and items:
            try:
                total = sum(float(it.get("price", 0.0) or 0.0) for it in items)
            except Exception:
                total = 0.0
        result = fiscal_info.get("result") if isinstance(fiscal_info, dict) else {}
        marks = extract_fiscal_marks(result) if extract_fiscal_marks is not None else {"zoi": "", "eor": ""}
        return {
            "invoice_number": str(invoice_number),
            "issue_datetime_iso": str(getattr(self, "_last_issue_datetime_iso", "") or ""),
            "items": items,
            "total": total,
            "fiscal_status": str((fiscal_info or {}).get("status") or getattr(self, "_last_fiscal_status", "unknown")),
            "zoi": str(marks.get("zoi", "")),
            "eor": str(marks.get("eor", "")),
        }

    def _worker_print_last_receipt(self, invoice_number: str):
        with self._receipt_print_lock:
            try:
                if print_receipt is None or ReceiptPrinterConfig is None:
                    raise RuntimeError("receipt_printer.py is not available")
                self._set_receipt_print_status("Preparing receipt")
                fiscal_info = self._wait_for_fiscal_result(invoice_number)
                receipt_data = self._build_last_receipt_data(invoice_number, fiscal_info)
                cfg = self._receipt_printer_config()
                if cfg is None:
                    raise RuntimeError("receipt printer config is not available")
                self._set_receipt_print_status("Printing receipt")
                ok = print_receipt(receipt_data, cfg)
                self._set_receipt_print_status("Receipt printed" if ok else "Receipt printer disabled")
            except Exception as e:
                self._set_receipt_print_status(f"Receipt print failed: {e}")
                print(f"[RECEIPT PRINTER] Failed: {e}")

    def on_receipt(self):
        invoice_number = str(getattr(self, "_last_invoice_number", "") or "").strip()
        if not invoice_number:
            self._set_receipt_print_status("No invoice available")
            print("[RECEIPT PRINTER] No invoice available to print")
            return
        threading.Thread(target=self._worker_print_last_receipt, args=(invoice_number,), daemon=True).start()

    def on_reprint_receipt(self):
        print("Reprint receipt requested")
        self.on_receipt()


    def _backend_visible_items_snapshot(self):
        snapshot = []

        def _append_items(items, panel_position: int, visible_count: int):
            for it in list(items or [])[:max(0, int(visible_count or 0))]:
                try:
                    row = deepcopy(it)
                except Exception:
                    try:
                        row = dict(it)
                    except Exception:
                        continue
                try:
                    row["panel_position"] = int(panel_position)
                    snapshot.append(row)
                except Exception:
                    continue

        local_position = int(getattr(base, "PANEL_POSITION", 0))
        local_visible_count = int(
            getattr(
                self,
                "panel_visible_count",
                getattr(base, "PANEL_VISIBLE_ITEMS", len(getattr(self, "items", []))) or len(getattr(self, "items", [])),
            )
        )
        _append_items(list(getattr(self, "items", []) or []), local_position, local_visible_count)

        if local_position == 0:
            for pos, panel in list(getattr(self, "remote_panels", {}).items()):
                try:
                    pos_i = int(pos)
                except Exception:
                    continue
                if pos_i == local_position or self._is_remote_panel_stale(panel):
                    continue
                panel_type = str((panel or {}).get("panel_type", getattr(base, "PANEL_TYPE", "A")) or "A").upper()
                layout = getattr(base, "PANEL_TYPE_CONFIGS", {}).get(panel_type, getattr(base, "PANEL_LAYOUT", {"visible_items": len((panel or {}).get("items") or [])}))
                visible_count = int((layout or {}).get("visible_items", len((panel or {}).get("items") or [])))
                _append_items((panel or {}).get("items") or [], pos_i, visible_count)

        return snapshot

    def _admin_accounts_file_path(self) -> str:
        return str(getattr(base, "ADMIN_ACCOUNTS_FILE", os.path.join(getattr(base, "PANEL_DIR", "."), "admin_accounts.txt")))

    def _load_local_admin_users_for_backend(self):
        users = []
        try:
            if hasattr(base, "load_admin_accounts"):
                loaded = base.load_admin_accounts() or {}
                for username, info in loaded.items():
                    if not username:
                        continue
                    users.append({
                        "username": str(username),
                        "password": str((info or {}).get("password", "")),
                        "role": str((info or {}).get("role", "worker")),
                        "is_active": True,
                    })
                    
                if users:
                    return users
        except Exception as e:
            print(f"[BACKEND] admin account helper load failed: {e}")

        path = self._admin_accounts_file_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) != 3:
                        continue
                    role, username, password = parts
                    if not username:
                        continue
                    users.append({
                        "username": username,
                        "password": password,
                        "role": role or "worker",
                        "is_active": True,
                    })
        except Exception as e:
            print(f"[BACKEND] admin account file read failed: {e}")
        return users

    def _should_sync_admin_users(self) -> bool:
        return bool(self.backend_client) and int(getattr(base, "PANEL_POSITION", 0)) == 0

    def _admin_users_fingerprint(self, users) -> str:
        parts = [f"{u.get('username','')}|{u.get('role','')}|{u.get('password','')}|{1 if u.get('is_active', True) else 0}" for u in sorted(users, key=lambda x: str(x.get('username','')))]
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    def _maybe_sync_admin_users(self, force: bool = False):
        if not self._should_sync_admin_users() or not self.backend_client:
            return
        path = self._admin_accounts_file_path()
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            mtime = None
        if (not force) and mtime is not None and self._admin_sync_last_mtime is not None and mtime == self._admin_sync_last_mtime:
            return

        users = self._load_local_admin_users_for_backend()
        fingerprint = self._admin_users_fingerprint(users)
        if (not force) and fingerprint == self._admin_sync_last_hash:
            self._admin_sync_last_mtime = mtime
            return

        try:
            self.backend_client.sync_admin_users(users, replace_missing=True)
            self._admin_sync_last_hash = fingerprint
            self._admin_sync_last_mtime = mtime
            print(f"[BACKEND] synced {len(users)} admin users for {BACKEND_PANEL_ID}")
        except Exception as e:
            print(f"[BACKEND] admin user sync failed: {e}")

    def _reservation_blocks_item(self, row: dict) -> bool:
        try:
            status = str((row or {}).get("status", "") or "").lower()
        except Exception:
            status = ""
        if status not in ("active", "claimed"):
            return False
        try:
            expires_at = (row or {}).get("expires_at")
            if expires_at:
                from datetime import datetime, timezone
                raw = str(expires_at).replace("Z", "+00:00")
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < datetime.now(timezone.utc):
                    return False
        except Exception:
            pass
        return True

    def _is_item_reserved(self, panel_position: int, item_id: int) -> bool:
        try:
            panel_position = int(panel_position)
            item_id = int(item_id)
        except Exception:
            return False
        for row in list(getattr(self, "server_reservation_codes", []) or []):
            try:
                if not self._reservation_blocks_item(row):
                    continue
                if int(row.get("panel_position", 0)) == panel_position and int(row.get("item_id")) == item_id:
                    return True
            except Exception:
                continue
        return False

    def _set_local_reservation_status(self, code: str, status: str):
        code = str(code or "").strip()
        if not code:
            return
        updated = []
        changed = False
        for row in list(getattr(self, "server_reservation_codes", []) or []):
            row_copy = dict(row or {})
            if str(row_copy.get("code", "")).strip() == code:
                row_copy["status"] = str(status)
                if str(status).lower() != "active":
                    row_copy.pop("claim_token", None)
                    row_copy.pop("claim_expires_at", None)
                changed = True
            updated.append(row_copy)
        if changed:
            self.server_reservation_codes = updated

    def _remove_local_reservation_code(self, code: str):
        code = str(code or "").strip()
        if not code:
            return
        before = list(getattr(self, "server_reservation_codes", []) or [])
        self.server_reservation_codes = [
            dict(row or {})
            for row in before
            if str((row or {}).get("code", "")).strip() != code
        ]

    def _append_reserved_suffix(self, text: str):
        reserved_txt = self.t("reserved")
        lines = [ln for ln in str(text or "").split("\n") if ln.strip()]
        if reserved_txt not in lines:
            lines.append(reserved_txt)
        return lines

    def _iter_widget_descendants(self, widget):
        for child in getattr(widget, "children", []) or []:
            yield child
            yield from self._iter_widget_descendants(child)

    def _finalize_reserved_button_visual(self, btn, *, item_id: int | None = None, allow_click: bool = False):
        try:
            descendants = list(self._iter_widget_descendants(btn))
            label_widgets = [w for w in descendants if isinstance(w, Label)]
            label_widgets = sorted(
                label_widgets,
                key=lambda w: (float(getattr(w, "center_y", 0.0)), float(getattr(w, "y", 0.0))),
                reverse=True,
            )

            reserved_text = self.t("reserved")
            number_text = f"#{int(item_id)}" if item_id is not None else ""

            if label_widgets:
                try:
                    label_widgets[0].text = number_text
                except Exception:
                    pass
            if len(label_widgets) >= 2:
                try:
                    label_widgets[1].text = reserved_text
                    label_widgets[1].halign = "center"
                    label_widgets[1].valign = "middle"
                except Exception:
                    pass
            for lb in label_widgets[2:]:
                try:
                    lb.text = ""
                except Exception:
                    pass

            # Hide the small price badge row if it exists.
            for box in descendants:
                if not isinstance(box, BoxLayout):
                    continue
                if box is btn:
                    continue
                try:
                    h = float(getattr(box, "height", 0) or 0)
                    w = float(getattr(box, "width", 0) or 0)
                    btn_w = float(getattr(btn, "width", 0) or 0)
                except Exception:
                    continue

                try:
                    contains_label = any(isinstance(x, Label) for x in self._iter_widget_descendants(box))
                except Exception:
                    contains_label = False

                if contains_label and 20 <= h <= 90 and (btn_w <= 0 or w < btn_w):
                    try:
                        box.opacity = 0
                    except Exception:
                        pass
                    try:
                        box.size_hint_y = None
                    except Exception:
                        pass
                    try:
                        box.height = 0
                    except Exception:
                        pass

            try:
                btn.disabled = (not allow_click)
            except Exception:
                pass
            try:
                btn.canvas.ask_update()
            except Exception:
                pass
        except Exception:
            pass

    def _decorate_reserved_button(self, btn, *, item_id: int | None = None, allow_click: bool = False):
        try:
            btn.disabled_rgba = [0.62, 0.62, 0.62, 0.62]
            btn.base_normal = [0.62, 0.62, 0.62, 0.62]
            btn.base_hover = [0.62, 0.62, 0.62, 0.62]
            btn.base_down = [0.62, 0.62, 0.62, 0.62]
            try:
                btn.border_rgba = [0.82, 0.82, 0.82, 0.72]
            except Exception:
                pass
            try:
                btn.badge_bg_rgba = [0, 0, 0, 0]
            except Exception:
                pass
            if hasattr(btn, "title_text"):
                btn.title_text = f"#{int(item_id)}" if item_id is not None else str(getattr(btn, "title_text", "") or "")
            if hasattr(btn, "price_text"):
                btn.price_text = ""
            if hasattr(btn, "status_text"):
                btn.status_text = self.t("reserved")
            if hasattr(btn, "title_font_size"):
                btn.title_font_size = "31sp"
            if hasattr(btn, "status_font_size"):
                btn.status_font_size = "18sp"

            try:
                btn.disabled = (not allow_click)
            except Exception:
                pass

            try:
                btn.refresh_bg()
            except Exception:
                pass

            Clock.schedule_once(
                lambda _dt, b=btn, iid=item_id, ac=allow_click: self._finalize_reserved_button_visual(b, item_id=iid, allow_click=ac),
                0,
            )
            Clock.schedule_once(
                lambda _dt, b=btn, iid=item_id, ac=allow_click: self._finalize_reserved_button_visual(b, item_id=iid, allow_click=ac),
                0.05,
            )
        except Exception:
            pass

    def _force_reservation_status_white(self):
        try:
            rs = self.root.get_screen("reservation")
        except Exception:
            return
        for wid in ("reservation_status", "reservation_body"):
            try:
                lbl = rs.ids[wid]
            except Exception:
                continue
            try:
                lbl.color = (1, 1, 1, 0.98)
            except Exception:
                pass
            try:
                lbl.disabled_color = (1, 1, 1, 0.98)
            except Exception:
                pass
            try:
                lbl.canvas.ask_update()
            except Exception:
                pass

    def add_current_to_cart(self):

        try:
            if self._is_item_reserved(getattr(self, "current_item_position", 0), getattr(self, "current_item_id", -1)):
                if self.root:
                    try:
                        scr = self.root.get_screen("item_detail")
                        scr.ids.item_note.text = self.t("reserved_not_for_sale")
                    except Exception:
                        pass
                return
        except Exception:
            pass
        return super().add_current_to_cart()

    def checkout_from_detail(self):
        try:
            if self._is_item_reserved(getattr(self, "current_item_position", 0), getattr(self, "current_item_id", -1)):
                if self.root:
                    try:
                        scr = self.root.get_screen("item_detail")
                        scr.ids.item_note.text = self.t("reserved_not_for_sale")
                    except Exception:
                        pass
                return
        except Exception:
            pass
        return super().checkout_from_detail()

    def start_cart_checkout(self):
        try:
            bad = []
            for entry in list(getattr(self, "cart_entries", []) or []):
                pos = int(entry.get("panel_position", getattr(base, "PANEL_POSITION", 0)))
                iid = int(entry.get("id"))
                if self._is_item_reserved(pos, iid):
                    bad.append((pos, iid))
            if bad:
                self.cart_entries = [
                    x for x in list(getattr(self, "cart_entries", []) or [])
                    if (int(x.get("panel_position", getattr(base, "PANEL_POSITION", 0))), int(x.get("id"))) not in bad
                ]
                self.update_cart_badge()
                self.refresh_cart_screen()
                self.goto_cart()
                return
        except Exception:
            pass
        return super().start_cart_checkout()

    def refresh_selection_grid(self):
        super().refresh_selection_grid()
        try:
            sel = self.root.get_screen("selection")
            left = sel.ids.items_grid_left
            right = sel.ids.items_grid_right
            visible = list(self.active_panel_items())
            left_buttons = [w for w in reversed(left.children)]
            right_buttons = [w for w in reversed(right.children)]
            current_pos = int(getattr(self, "current_panel_position", getattr(base, "PANEL_POSITION", 0)))
            for idx, it in enumerate(visible):
                btn = left_buttons[idx] if idx < int(self.panel_left_count) else right_buttons[idx - int(self.panel_left_count)]
                if self._is_item_reserved(current_pos, int(it["id"])):
                    self._decorate_reserved_button(btn, item_id=int(it["id"]))
        except Exception as e:
            print(f"[RESERVED] selection decorate failed: {e}")

    def refresh_check_price_grid(self):
        super().refresh_check_price_grid()
        try:
            scr = self.root.get_screen("price_check")
            left = scr.ids.check_grid_left
            right = scr.ids.check_grid_right
            visible = list(self.active_panel_items())
            left_buttons = [w for w in reversed(left.children)]
            right_buttons = [w for w in reversed(right.children)]
            current_pos = int(getattr(self, "current_panel_position", getattr(base, "PANEL_POSITION", 0)))
            for idx, it in enumerate(visible):
                btn = left_buttons[idx] if idx < int(self.panel_left_count) else right_buttons[idx - int(self.panel_left_count)]
                if self._is_item_reserved(current_pos, int(it["id"])):
                    self._decorate_reserved_button(btn, item_id=int(it["id"]))
        except Exception as e:
            print(f"[RESERVED] price decorate failed: {e}")

    def refresh_admin_grid(self):
        super().refresh_admin_grid()
        try:
            scr = self.root.get_screen("admin_panel")
            left = scr.ids.admin_grid_left
            right = scr.ids.admin_grid_right
            visible = list(self.active_panel_items())
            left_buttons = [w for w in reversed(left.children)]
            right_buttons = [w for w in reversed(right.children)]
            current_pos = int(getattr(self, "current_panel_position", getattr(base, "PANEL_POSITION", 0)))
            for idx, it in enumerate(visible):
                btn = left_buttons[idx] if idx < int(self.panel_left_count) else right_buttons[idx - int(self.panel_left_count)]
                if self._is_item_reserved(current_pos, int(it["id"])):
                    self._decorate_reserved_button(btn, item_id=int(it["id"]), allow_click=True)
        except Exception as e:
            print(f"[RESERVED] admin decorate failed: {e}")

    def _update_item_detail_screen(self):
        super()._update_item_detail_screen()
        try:
            scr = self.root.get_screen("item_detail")
            panel_position = int(getattr(self, "current_item_position", getattr(base, "PANEL_POSITION", 0)))
            item_id = getattr(self, "current_item_id", None)
            if item_id is None:
                return
            if self._is_item_reserved(panel_position, int(item_id)):
                scr.ids.item_note.text = self.t("reserved_not_for_sale")
                scr.ids.item_add_btn.disabled = True
                try:
                    scr.ids.item_checkout_btn.disabled = (len(getattr(self, "cart_entries", [])) == 0)
                except Exception:
                    scr.ids.item_checkout_btn.disabled = True
        except Exception as e:
            print(f"[RESERVED] detail decorate failed: {e}")


# =========================
# Additional UI tweaks patch
# =========================
base.I18N.setdefault("module_slots", {
    "en": "Module {panel_type} - {slots} slots",
    "si": "Modul {panel_type} - {slots} mest",
    "de": "Modul {panel_type} - {slots} Plätze",
    "hr": "Modul {panel_type} - {slots} mjesta",
})

def _backend_patch_module_info_kv():
    try:
        kv = base.KV
    except Exception:
        return

    replacements = {
        """
                Widget:

                HoverButton:
                    id: selection_right_btn
""": """
                Label:
                    id: selection_module_info
                    text: ""
                    color: 1, 1, 1, 0.96
                    font_size: "24sp"
                    bold: True
                    halign: "center"
                    valign: "middle"
                    text_size: self.size

                HoverButton:
                    id: selection_right_btn
""",
        """
                Widget:

                HoverButton:
                    id: check_price_right_btn
""": """
                Label:
                    id: check_price_module_info
                    text: ""
                    color: 1, 1, 1, 0.96
                    font_size: "24sp"
                    bold: True
                    halign: "center"
                    valign: "middle"
                    text_size: self.size

                HoverButton:
                    id: check_price_right_btn
""",
        """
                Widget:

                HoverButton:
                    id: admin_right_btn
""": """
                Label:
                    id: admin_module_info
                    text: ""
                    color: 1, 1, 1, 0.96
                    font_size: "24sp"
                    bold: True
                    halign: "center"
                    valign: "middle"
                    text_size: self.size

                HoverButton:
                    id: admin_right_btn
""",
    }

    changed = False
    for old, new in replacements.items():
        if old in kv and new not in kv:
            kv = kv.replace(old, new)
            changed = True
    if changed:
        base.KV = kv

_backend_patch_module_info_kv()


def _button_labels_top_to_bottom(widget):
    labels = []
    try:
        descendants = widget.walk(restrict=True)
    except Exception:
        descendants = []
    for child in descendants:
        if isinstance(child, Label):
            labels.append(child)
    labels = sorted(
        labels,
        key=lambda w: (float(getattr(w, "center_y", 0.0)), float(getattr(w, "y", 0.0))),
        reverse=True,
    )
    return labels


def _force_admin_login_status_white(self):
    try:
        scr = self.root.get_screen("admin_login")
        lbl = scr.ids.login_status
        lbl.color = (1, 1, 1, 0.98)
        try:
            lbl.disabled_color = (1, 1, 1, 0.98)
        except Exception:
            pass
        try:
            lbl.canvas.ask_update()
        except Exception:
            pass
    except Exception:
        pass


def _refresh_module_info_labels(self):
    if not getattr(self, "root", None):
        return
    try:
        panel_type = str(self.current_panel_type() or getattr(self, "panel_type", "A"))
    except Exception:
        panel_type = str(getattr(self, "panel_type", "A"))
    try:
        slots = int(getattr(self, "panel_visible_count", 0) or 0)
    except Exception:
        slots = 0

    text = self.t("module_slots").format(panel_type=panel_type, slots=slots)
    targets = (
        ("selection", "selection_module_info"),
        ("price_check", "check_price_module_info"),
        ("admin_panel", "admin_module_info"),
    )
    for screen_name, wid in targets:
        try:
            scr = self.root.get_screen(screen_name)
            if wid in scr.ids:
                scr.ids[wid].text = text
        except Exception:
            pass


def _move_admin_reopen_button_right(self):
    try:
        scr = self.root.get_screen("admin_edit_item")
        btn = scr.ids.edit_reenable_btn
        parent = btn.parent
        if not parent:
            return
        parent.remove_widget(btn)
        parent.add_widget(btn, index=0)
    except Exception:
        pass


def _style_available_button_number(self, btn, *, enabled=True, reserved=False):
    try:
        labels = _button_labels_top_to_bottom(btn)
        if not labels:
            return
        top = labels[0]
        if reserved:
            top.color = (0.92, 0.92, 0.92, 0.90)
        elif enabled:
            top.color = (0.78, 0.94, 0.78, 0.98)
        else:
            top.color = (0.92, 0.92, 0.92, 0.88)
        try:
            top.canvas.ask_update()
        except Exception:
            pass
    except Exception:
        pass


def _hide_price_badge_only(self, btn):
    try:
        descendants = list(self._iter_widget_descendants(btn))
    except Exception:
        descendants = []
    for box in descendants:
        if not isinstance(box, BoxLayout):
            continue
        if box is btn:
            continue
        try:
            h = float(getattr(box, "height", 0) or 0)
            w = float(getattr(box, "width", 0) or 0)
            btn_w = float(getattr(btn, "width", 0) or 0)
        except Exception:
            continue
        try:
            contains_label = any(isinstance(x, Label) for x in self._iter_widget_descendants(box))
        except Exception:
            contains_label = False
        if contains_label and 20 <= h <= 100 and (btn_w <= 0 or w < btn_w):
            try:
                box.opacity = 0
                box.size_hint_y = None
                box.height = 0
            except Exception:
                pass


def _finalize_reserved_button_visual_patched(self, btn, *, item_id: int | None = None, allow_click: bool = False):
    try:
        self._hide_price_badge_only(btn)
        try:
            btn.disabled = (not allow_click)
        except Exception:
            pass
        self._style_available_button_number(btn, enabled=False, reserved=True)
        try:
            btn.canvas.ask_update()
        except Exception:
            pass
    except Exception:
        pass


def _decorate_reserved_button_patched(self, btn, *, item_id: int | None = None, allow_click: bool = False):
    try:
        btn.disabled_rgba = [0.62, 0.62, 0.62, 0.62]
        btn.base_normal = [0.62, 0.62, 0.62, 0.62]
        btn.base_hover = [0.70, 0.70, 0.70, 0.76]
        btn.base_down = [0.56, 0.56, 0.56, 0.82]
        try:
            btn.border_rgba = [0.82, 0.82, 0.82, 0.72]
        except Exception:
            pass
        try:
            btn.badge_bg_rgba = [0, 0, 0, 0]
        except Exception:
            pass

        if hasattr(btn, "title_text"):
            btn.title_text = f"#{int(item_id)}" if item_id is not None else str(getattr(btn, "title_text", "") or "")
        if hasattr(btn, "price_text"):
            btn.price_text = ""
        if hasattr(btn, "status_text"):
            btn.status_text = self.t("reserved")
        if hasattr(btn, "title_font_size"):
            btn.title_font_size = "31sp"
        if hasattr(btn, "status_font_size"):
            btn.status_font_size = "18sp"

        try:
            btn.disabled = (not allow_click)
        except Exception:
            pass
        try:
            btn.refresh_bg()
        except Exception:
            pass

        Clock.schedule_once(lambda _dt, b=btn, iid=item_id, ac=allow_click: self._finalize_reserved_button_visual(b, item_id=iid, allow_click=ac), 0)
        Clock.schedule_once(lambda _dt, b=btn, iid=item_id, ac=allow_click: self._finalize_reserved_button_visual(b, item_id=iid, allow_click=ac), 0.05)
        Clock.schedule_once(lambda _dt, b=btn, iid=item_id, ac=allow_click: self._finalize_reserved_button_visual(b, item_id=iid, allow_click=ac), 0.15)
    except Exception:
        pass


# monkey-patch reserved visuals and utility helpers
PanelBackendReservationApp._finalize_reserved_button_visual = _finalize_reserved_button_visual_patched
PanelBackendReservationApp._decorate_reserved_button = _decorate_reserved_button_patched
PanelBackendReservationApp._force_admin_login_status_white = _force_admin_login_status_white
PanelBackendReservationApp._refresh_module_info_labels = _refresh_module_info_labels
PanelBackendReservationApp._move_admin_reopen_button_right = _move_admin_reopen_button_right
PanelBackendReservationApp._style_available_button_number = _style_available_button_number
PanelBackendReservationApp._hide_price_badge_only = _hide_price_badge_only

# wrap existing methods with lightweight UI refreshes
_OLD_BUILD = PanelBackendReservationApp.build
def _build_patched(self):
    root = _OLD_BUILD(self)
    for delay in (0, 0.05, 0.2):
        try:
            Clock.schedule_once(lambda _dt, app=self: app._move_admin_reopen_button_right(), delay)
            Clock.schedule_once(lambda _dt, app=self: app._refresh_module_info_labels(), delay)
            Clock.schedule_once(lambda _dt, app=self: app._force_admin_login_status_white(), delay)
        except Exception:
            pass
    return root
PanelBackendReservationApp.build = _build_patched

_OLD_ON_START = PanelBackendReservationApp.on_start
def _on_start_patched(self):
    _OLD_ON_START(self)
    for delay in (0, 0.1, 0.3, 1.0):
        try:
            Clock.schedule_once(lambda _dt, app=self: app._move_admin_reopen_button_right(), delay)
            Clock.schedule_once(lambda _dt, app=self: app._refresh_module_info_labels(), delay)
            Clock.schedule_once(lambda _dt, app=self: app._force_admin_login_status_white(), delay)
        except Exception:
            pass
PanelBackendReservationApp.on_start = _on_start_patched

_OLD_APPLY_TRANSLATIONS = PanelBackendReservationApp.apply_translations
def _apply_translations_patched(self):
    _OLD_APPLY_TRANSLATIONS(self)
    try:
        self._refresh_module_info_labels()
    except Exception:
        pass
    try:
        self._force_admin_login_status_white()
    except Exception:
        pass
PanelBackendReservationApp.apply_translations = _apply_translations_patched

_OLD_SWITCH_PANEL_VIEW = PanelBackendReservationApp.switch_panel_view
def _switch_panel_view_patched(self, direction: int):
    _OLD_SWITCH_PANEL_VIEW(self, direction)
    try:
        self._refresh_module_info_labels()
    except Exception:
        pass
PanelBackendReservationApp.switch_panel_view = _switch_panel_view_patched

_OLD_GOTO_SELECTION = PanelBackendReservationApp.goto_selection
def _goto_selection_patched(self):
    _OLD_GOTO_SELECTION(self)
    try:
        self._refresh_module_info_labels()
    except Exception:
        pass
PanelBackendReservationApp.goto_selection = _goto_selection_patched

if hasattr(PanelBackendReservationApp, "goto_check_price"):
    _OLD_GOTO_CHECK_PRICE = PanelBackendReservationApp.goto_check_price
    def _goto_check_price_patched(self):
        _OLD_GOTO_CHECK_PRICE(self)
        try:
            self._refresh_module_info_labels()
        except Exception:
            pass
    PanelBackendReservationApp.goto_check_price = _goto_check_price_patched

_OLD_GOTO_ADMIN_PANEL = PanelBackendReservationApp.goto_admin_panel
def _goto_admin_panel_patched(self):
    _OLD_GOTO_ADMIN_PANEL(self)
    try:
        self._refresh_module_info_labels()
    except Exception:
        pass
    try:
        self._move_admin_reopen_button_right()
    except Exception:
        pass
PanelBackendReservationApp.goto_admin_panel = _goto_admin_panel_patched

_OLD_ADMIN_LOGIN = PanelBackendReservationApp.admin_login
def _admin_login_patched(self):
    _OLD_ADMIN_LOGIN(self)
    try:
        self._force_admin_login_status_white()
    except Exception:
        pass
PanelBackendReservationApp.admin_login = _admin_login_patched

_OLD_RES_INVALID = PanelBackendReservationApp._reservation_verify_then_collect_invalid
def _reservation_verify_then_collect_invalid_patched(self, result: dict):
    _OLD_RES_INVALID(self, result)
    try:
        self._force_reservation_status_white()
    except Exception:
        pass
PanelBackendReservationApp._reservation_verify_then_collect_invalid = _reservation_verify_then_collect_invalid_patched

_OLD_RES_FAILED = PanelBackendReservationApp._reservation_collect_failed
def _reservation_collect_failed_patched(self, message: str):
    _OLD_RES_FAILED(self, message)
    try:
        self._force_reservation_status_white()
    except Exception:
        pass
PanelBackendReservationApp._reservation_collect_failed = _reservation_collect_failed_patched

_OLD_RES_LOCAL = PanelBackendReservationApp._reservation_local_open_done
def _reservation_local_open_done_patched(self, item_id, label, panel_position, code, claim_token, results):
    _OLD_RES_LOCAL(self, item_id, label, panel_position, code, claim_token, results)
    try:
        self._force_reservation_status_white()
    except Exception:
        pass
PanelBackendReservationApp._reservation_local_open_done = _reservation_local_open_done_patched

_OLD_RES_REMOTE = PanelBackendReservationApp._reservation_remote_open_done
def _reservation_remote_open_done_patched(self, item_id, label, panel_position, code, claim_token, results):
    _OLD_RES_REMOTE(self, item_id, label, panel_position, code, claim_token, results)
    try:
        self._force_reservation_status_white()
    except Exception:
        pass
PanelBackendReservationApp._reservation_remote_open_done = _reservation_remote_open_done_patched

_OLD_REFRESH_SELECTION = PanelBackendReservationApp.refresh_selection_grid
def _refresh_selection_grid_patched(self):
    _OLD_REFRESH_SELECTION(self)
    try:
        sel = self.root.get_screen("selection")
        left = sel.ids.items_grid_left
        right = sel.ids.items_grid_right
        visible = list(self.active_panel_items())
        left_buttons = [w for w in reversed(left.children)]
        right_buttons = [w for w in reversed(right.children)]
        current_pos = int(getattr(self, "current_panel_position", getattr(base, "PANEL_POSITION", 0)))
        for idx, it in enumerate(visible):
            btn = left_buttons[idx] if idx < int(self.panel_left_count) else right_buttons[idx - int(self.panel_left_count)]
            reserved = self._is_item_reserved(current_pos, int(it["id"]))
            self._style_available_button_number(btn, enabled=bool(it.get("enabled", True)) and not reserved, reserved=reserved)
    except Exception:
        pass
    try:
        self._refresh_module_info_labels()
    except Exception:
        pass
PanelBackendReservationApp.refresh_selection_grid = _refresh_selection_grid_patched

_OLD_REFRESH_CHECK = PanelBackendReservationApp.refresh_check_price_grid
def _refresh_check_price_grid_patched(self):
    _OLD_REFRESH_CHECK(self)
    try:
        scr = self.root.get_screen("price_check")
        left = scr.ids.check_grid_left
        right = scr.ids.check_grid_right
        visible = list(self.active_panel_items())
        left_buttons = [w for w in reversed(left.children)]
        right_buttons = [w for w in reversed(right.children)]
        current_pos = int(getattr(self, "current_panel_position", getattr(base, "PANEL_POSITION", 0)))
        for idx, it in enumerate(visible):
            btn = left_buttons[idx] if idx < int(self.panel_left_count) else right_buttons[idx - int(self.panel_left_count)]
            reserved = self._is_item_reserved(current_pos, int(it["id"]))
            self._style_available_button_number(btn, enabled=bool(it.get("enabled", True)) and not reserved, reserved=reserved)
    except Exception:
        pass
    try:
        self._refresh_module_info_labels()
    except Exception:
        pass
PanelBackendReservationApp.refresh_check_price_grid = _refresh_check_price_grid_patched

_OLD_REFRESH_ADMIN = PanelBackendReservationApp.refresh_admin_grid
def _refresh_admin_grid_patched(self):
    _OLD_REFRESH_ADMIN(self)
    try:
        scr = self.root.get_screen("admin_panel")
        left = scr.ids.admin_grid_left
        right = scr.ids.admin_grid_right
        visible = list(self.active_panel_items())
        left_buttons = [w for w in reversed(left.children)]
        right_buttons = [w for w in reversed(right.children)]
        current_pos = int(getattr(self, "current_panel_position", getattr(base, "PANEL_POSITION", 0)))
        for idx, it in enumerate(visible):
            btn = left_buttons[idx] if idx < int(self.panel_left_count) else right_buttons[idx - int(self.panel_left_count)]
            reserved = self._is_item_reserved(current_pos, int(it["id"]))
            self._style_available_button_number(btn, enabled=bool(it.get("enabled", True)) and not reserved, reserved=reserved)
    except Exception:
        pass
    try:
        self._refresh_module_info_labels()
    except Exception:
        pass
PanelBackendReservationApp.refresh_admin_grid = _refresh_admin_grid_patched


# ---- Final HR flag + reserved visual patch ----
_OLD_BACKEND_INIT_HR = PanelBackendReservationApp.__init__
def _backend_init_hr(self, **kwargs):
    _OLD_BACKEND_INIT_HR(self, **kwargs)
    try:
        self.ICON_HR = getattr(base, "ICON_HR", os.path.join(getattr(base, "PANEL_DIR", "."), "flag_hr.png"))
    except Exception:
        pass
PanelBackendReservationApp.__init__ = _backend_init_hr

def _reserved_button_labels_fix(self, btn, *, item_id: int | None = None, allow_click: bool = False):
    try:
        if hasattr(btn, "number_text"):
            btn.number_text = f"#{int(item_id)}" if item_id is not None else str(getattr(btn, "number_text", "") or "")
        if hasattr(btn, "title_text"):
            btn.title_text = self.t("reserved")
        if hasattr(btn, "price_text"):
            btn.price_text = ""
        if hasattr(btn, "status_text"):
            btn.status_text = ""
        if hasattr(btn, "title_color_rgba"):
            btn.title_color_rgba = [0.95, 0.95, 0.95, 0.96]
        if hasattr(btn, "number_color_rgba"):
            btn.number_color_rgba = [0.92, 0.92, 0.92, 0.90]
        if hasattr(btn, "title_font_size"):
            btn.title_font_size = "30sp"
        if hasattr(btn, "status_font_size"):
            btn.status_font_size = "18sp"
        try:
            btn.disabled = (not allow_click)
        except Exception:
            pass
        try:
            btn.refresh_bg()
        except Exception:
            pass
        try:
            self._hide_price_badge_only(btn)
        except Exception:
            pass
        try:
            btn.canvas.ask_update()
        except Exception:
            pass
    except Exception:
        pass

def _decorate_reserved_button_final(self, btn, *, item_id: int | None = None, allow_click: bool = False):
    try:
        btn.disabled_rgba = [0.62, 0.62, 0.62, 0.62]
        btn.base_normal = [0.62, 0.62, 0.62, 0.62]
        btn.base_hover = [0.70, 0.70, 0.70, 0.76]
        btn.base_down = [0.56, 0.56, 0.56, 0.82]
        try:
            btn.border_rgba = [0.82, 0.82, 0.82, 0.72]
        except Exception:
            pass
        try:
            btn.badge_bg_rgba = [0, 0, 0, 0]
        except Exception:
            pass
        self._reserved_button_labels_fix(btn, item_id=item_id, allow_click=allow_click)
        Clock.schedule_once(lambda _dt, b=btn, iid=item_id, ac=allow_click: self._reserved_button_labels_fix(b, item_id=iid, allow_click=ac), 0)
        Clock.schedule_once(lambda _dt, b=btn, iid=item_id, ac=allow_click: self._reserved_button_labels_fix(b, item_id=iid, allow_click=ac), 0.05)
        Clock.schedule_once(lambda _dt, b=btn, iid=item_id, ac=allow_click: self._reserved_button_labels_fix(b, item_id=iid, allow_click=ac), 0.15)
    except Exception:
        pass

PanelBackendReservationApp._reserved_button_labels_fix = _reserved_button_labels_fix
PanelBackendReservationApp._decorate_reserved_button = _decorate_reserved_button_final
PanelBackendReservationApp._finalize_reserved_button_visual = _reserved_button_labels_fix


# ---- Final patch: Croatian completeness + reservation numpad + reserved-as-empty visuals ----
_RESERVATION_NUMPAD_PATCH = r'''
<ReservationKey@HoverButton>:
    font_size: "28sp"
    halign: "center"
    valign: "middle"
    text_size: self.size
    color: 1, 1, 1, 0.98
    base_normal: [0.84, 0.84, 0.84, 0.30]
    base_hover: [0.90, 0.90, 0.90, 0.36]
    base_down: [0.96, 0.96, 0.96, 0.42]
    disabled_rgba: [0.75, 0.75, 0.75, 0.12]
    border_rgba: [1, 1, 1, 0.65]

<ReservationIconKey@FloatLayout>:
    icon: ""
    key_value: ""
    disabled: False

    HoverButton:
        text: ""
        disabled: root.disabled
        size_hint: 1, 1
        pos_hint: {"x": 0, "y": 0}
        base_normal: [0.84, 0.84, 0.84, 0.30]
        base_hover: [0.90, 0.90, 0.90, 0.36]
        base_down: [0.96, 0.96, 0.96, 0.42]
        disabled_rgba: [0.75, 0.75, 0.75, 0.12]
        border_rgba: [1, 1, 1, 0.65]
        on_release: app.admin_keyboard_key(root.key_value) if not root.disabled else None

    AnchorLayout:
        anchor_x: "center"
        anchor_y: "center"
        size_hint: 1, 1
        pos_hint: {"x": 0, "y": 0}

        CrispImage:
            source: root.icon
            size_hint: None, None
            size: dp(52), dp(52)
            fit_mode: "contain"
            opacity: 1 if not root.disabled else 0.45

<ReservationNumpad@BoxLayout>:
    orientation: "vertical"
    size_hint_y: None
    height: dp(330)
    spacing: dp(10)
    padding: [dp(16), dp(16), dp(16), dp(16)]

    canvas.before:
        Color:
            rgba: 0, 0, 0, 0.14
        RoundedRectangle:
            pos: self.x, self.y + dp(-3)
            size: self.size
            radius: [dp(28), dp(28), dp(28), dp(28)]
        Color:
            rgba: 0.84, 0.84, 0.84, 0.18
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(28), dp(28), dp(28), dp(28)]
        Color:
            rgba: 1, 1, 1, 0.65
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, dp(28))
            width: dp(1.5)

    GridLayout:
        cols: 3
        rows: 4
        spacing: dp(10)

        ReservationKey:
            text: "1"
            on_release: app.admin_keyboard_key(self.text)
        ReservationKey:
            text: "2"
            on_release: app.admin_keyboard_key(self.text)
        ReservationKey:
            text: "3"
            on_release: app.admin_keyboard_key(self.text)
        ReservationKey:
            text: "4"
            on_release: app.admin_keyboard_key(self.text)
        ReservationKey:
            text: "5"
            on_release: app.admin_keyboard_key(self.text)
        ReservationKey:
            text: "6"
            on_release: app.admin_keyboard_key(self.text)
        ReservationKey:
            text: "7"
            on_release: app.admin_keyboard_key(self.text)
        ReservationKey:
            text: "8"
            on_release: app.admin_keyboard_key(self.text)
        ReservationKey:
            text: "9"
            on_release: app.admin_keyboard_key(self.text)
        ReservationKey:
            text: app.keyboard_clear_label
            on_release: app.admin_keyboard_key("Clear")
        ReservationKey:
            text: "0"
            on_release: app.admin_keyboard_key(self.text)
        ReservationIconKey:
            icon: app.ICON_KEY_BACKSPACE
            key_value: "Backspace"
'''

_RESERVATION_SCREEN_BLOCK_NUMPAD = '''
<ReservationScreen>:
    name: "reservation"
    FullScreenFloat:
        FloatLayout:
            BoxLayout:
                orientation: "vertical"
                padding: [dp(30), TOP_PAD, dp(30), dp(30)]
                spacing: dp(20)
                size_hint: 1, 1

                Widget:
                    size_hint_y: None
                    height: dp(56)

                TitleLabel:
                    id: reservation_title
                    text: "Reservation"
                    size_hint_y: None
                    height: 0
                    opacity: 0

                BoxLayout:
                    size_hint_y: None
                    height: "80dp"
                    Widget:
                    RoundedTextInput:
                        id: reservation_code_input
                        hint_text: "Enter reservation code"
                        font_size: OTHER_TXT
                        fill_rgba: [0.2, 0.33, 0.23, 0.84]
                        border_rgba: [0.84, 0.71, 0.36, 0.95]
                        foreground_color: 1, 1, 1, 1
                        disabled_foreground_color: 1, 1, 1, 1
                        cursor_color: 1, 1, 1, 1
                        hint_text_color: 1, 1, 1, 0.78
                        hint_text_color_normal: 1, 1, 1, 0.78
                        hint_text_color_focus: 1, 1, 1, 0.78
                        selection_color: 1, 1, 1, 0.18
                        input_filter: "int"
                        size_hint_x: None
                        width: min(dp(860), Window.width - dp(140))
                    Widget:

                Label:
                    id: reservation_status
                    text: ""
                    font_size: OTHER_TXT
                    bold: True
                    color: 1, 1, 1, 0.96
                    halign: "center"
                    valign: "middle"
                    text_size: self.size
                    size_hint_y: None
                    height: "120dp"

                Widget:
                    size_hint_y: 0.08

                ReservationNumpad:

                BoxLayout:
                    size_hint_y: None
                    height: "110dp"
                    spacing: dp(16)

                    NavIconButton:
                        id: reservation_back_btn
                        icon: app.ICON_BACK
                        text: "Back"
                        on_release: app.goto_menu()

                    NavIconButton:
                        id: reservation_home_btn
                        icon: app.ICON_HOME
                        text: "Home"
                        on_release: app.goto_menu()

                    NavIconButton:
                        id: reservation_collect_btn
                        icon: app.ICON_RESERVATION
                        text: "Collect"
                        on_release: app.reservation_collect()

        HeaderBar:
'''.strip("\n")

if '<ReservationNumpad@BoxLayout>:' not in base.KV:
    base.KV = _RESERVATION_NUMPAD_PATCH + "\n" + base.KV
base.KV = re.sub(r"<ReservationScreen>:\n.*?(?=\n<PriceCheckScreen>:)", _RESERVATION_SCREEN_BLOCK_NUMPAD + "\n", base.KV, flags=re.S)

def _apply_reserved_button_visual(self, btn, *, item_id: int | None = None, allow_click: bool = False):
    try:
        btn.disabled = False
        btn.base_normal = [0.45, 0.45, 0.45, 0.58]
        btn.base_hover = [0.48, 0.48, 0.48, 0.66]
        btn.base_down = [0.40, 0.40, 0.40, 0.78]
        btn.disabled_rgba = [0.45, 0.45, 0.45, 0.58]
        btn.border_rgba = [0.70, 0.70, 0.70, 0.65]
        btn.number_color_rgba = [0.82, 0.82, 0.82, 0.95]
        btn.title_color_rgba = [0.88, 0.88, 0.88, 0.95]
        btn.price_color_rgba = [0.50, 0.45, 0.39, 1.0]
        btn.number_text = f"#{int(item_id)}" if item_id is not None else str(getattr(btn, "number_text", "") or "")
        btn.title_text = self.t("reserved")
        btn.price_text = ""
        btn.status_text = ""
        try:
            btn.badge_bg_rgba = [0, 0, 0, 0]
        except Exception:
            pass
        try:
            self._hide_price_badge_only(btn)
        except Exception:
            pass
        try:
            self._style_available_button_number(btn, enabled=False, reserved=True)
        except Exception:
            pass
        btn.refresh_bg()
    except Exception:
        pass

def _refresh_selection_grid_reserved_final(self):
    sel = self.root.get_screen("selection")
    sel.ids.selection_title.text = self._panel_title(self.t("select_bouquet"))
    left = sel.ids.items_grid_left
    right = sel.ids.items_grid_right
    left.clear_widgets()
    right.clear_widgets()
    self._refresh_panel_nav_buttons()
    visible = list(self.active_panel_items())
    current_pos = int(getattr(self, "current_panel_position", getattr(base, "PANEL_POSITION", 0)))
    for idx, it in enumerate(visible):
        item_id = int(it["id"])
        reserved = self._is_item_reserved(current_pos, item_id)
        in_cart = (not reserved) and any(int(x.get("panel_position")) == int(self.current_panel_position) and int(x.get("id")) == item_id for x in self.cart_entries)
        if reserved:
            number_text, name_text, price_text = "", self.t("reserved"), ""
        else:
            number_text, name_text, price_text = self._bouquet_card_parts(it, empty_label=self.t("empty"))
            number_text = ""
            if in_cart and it["enabled"]:
                name_text = self.t("in_cart")
                price_text = ""
        btn = base.BouquetButton(number_text=number_text, title_text=name_text, price_text=price_text, number_font_size="1sp", title_font_size="43sp", price_font_size="34sp", status_text="", status_font_size="15sp")
        btn.disabled = False
        if reserved:
            self._apply_reserved_button_visual(btn, item_id=item_id, allow_click=False)
            btn.number_text = ""
            btn.title_font_size = "43sp"
            btn.price_font_size = "34sp"
        else:
            self._apply_bouquet_button_palette(btn, is_empty=(not it["enabled"]), is_in_cart=in_cart)
            btn.number_text = ""
            btn.title_color_rgba = [1.0, 1.0, 1.0, 0.96]
            btn.price_color_rgba = [0.50, 0.45, 0.39, 1.0]
            self._style_available_button_number(btn, enabled=bool(it.get("enabled", True)), reserved=False)
            btn.refresh_bg()
            if it["enabled"]:
                btn.bind(on_release=lambda _btn, iid=item_id: self.goto_item_detail(iid))
        self._add_bouquet_button_to_grid(btn, idx, left, right)
    try:
        self._refresh_module_info_labels()
    except Exception:
        pass

def _refresh_check_price_grid_reserved_final(self):
    ps = self.root.get_screen("price_check")
    ps.ids.check_price_title.text = self._panel_title(self.t("check_price_title"))
    left = ps.ids.check_grid_left
    right = ps.ids.check_grid_right
    left.clear_widgets()
    right.clear_widgets()
    self._refresh_panel_nav_buttons()
    visible = list(self.active_panel_items())
    current_pos = int(getattr(self, "current_panel_position", getattr(base, "PANEL_POSITION", 0)))
    for idx, it in enumerate(visible):
        item_id = int(it["id"])
        reserved = self._is_item_reserved(current_pos, item_id)
        in_cart = (not reserved) and any(int(x.get("panel_position")) == int(self.current_panel_position) and int(x.get("id")) == item_id for x in self.cart_entries)
        if reserved:
            number_text, name_text, price_text = f"#{item_id}", self.t("reserved"), ""
        else:
            number_text, name_text, price_text = self._bouquet_card_parts(it, empty_label=self.t("empty"))
            if in_cart and it["enabled"]:
                name_text = self.t("in_cart")
                price_text = ""
        btn = base.BouquetButton(number_text=number_text, title_text=name_text, price_text=price_text, number_font_size="26sp", title_font_size="35sp", price_font_size="26sp", status_text="", status_font_size="15sp")
        btn.disabled = False
        if reserved:
            self._apply_reserved_button_visual(btn, item_id=item_id, allow_click=False)
        else:
            self._apply_bouquet_button_palette(btn, is_empty=(not it["enabled"]), is_in_cart=in_cart)
            self._style_available_button_number(btn, enabled=bool(it.get("enabled", True)), reserved=False)
            btn.refresh_bg()
        self._add_bouquet_button_to_grid(btn, idx, left, right)
    try:
        self._refresh_module_info_labels()
    except Exception:
        pass

def _refresh_admin_grid_reserved_final(self):
    ap = self.root.get_screen("admin_panel")
    left = ap.ids.admin_grid_left
    right = ap.ids.admin_grid_right
    left.clear_widgets()
    right.clear_widgets()
    worker_mode = (self.current_admin_role == base.ADMIN_ROLE_WORKER)
    visible = list(self.active_panel_items())
    current_pos = int(getattr(self, "current_panel_position", getattr(base, "PANEL_POSITION", 0)))
    for idx, it in enumerate(visible):
        item_id = int(it["id"])
        reserved = self._is_item_reserved(current_pos, item_id)
        is_empty = (not bool(it["enabled"])) or reserved
        if reserved:
            number_text, name_text, price_text = f"#{item_id}", self.t("reserved"), ""
        else:
            number_text, name_text, price_text = self._bouquet_card_parts(it, empty_label=self.t("empty"))
        btn = base.BouquetButton(number_text=number_text, title_text=name_text, price_text=price_text, number_font_size="26sp", title_font_size="35sp", price_font_size="26sp", status_text="", status_font_size="15sp")
        is_pending = (item_id in self._admin_door_pending_ids)
        was_success = (item_id in self._admin_door_success_ids)
        if reserved and not is_pending and not was_success:
            self._apply_reserved_button_visual(btn, item_id=item_id, allow_click=True)
        else:
            self._apply_bouquet_button_palette(btn, is_empty=is_empty, is_pending=is_pending, was_success=was_success)
            self._style_available_button_number(btn, enabled=(bool(it.get("enabled", True)) and not reserved), reserved=reserved)
            btn.refresh_bg()
        if worker_mode:
            btn.bind(on_release=lambda _btn, iid=item_id: self.admin_worker_reopen_door(iid))
        else:
            btn.bind(on_release=lambda _btn, iid=item_id: self.goto_admin_edit(iid))
        self._add_bouquet_button_to_grid(btn, idx, left, right)
    try:
        self._refresh_module_info_labels()
    except Exception:
        pass

PanelBackendReservationApp._apply_reserved_button_visual = _apply_reserved_button_visual
PanelBackendReservationApp._decorate_reserved_button = _apply_reserved_button_visual
PanelBackendReservationApp._finalize_reserved_button_visual = _apply_reserved_button_visual
PanelBackendReservationApp.refresh_selection_grid = _refresh_selection_grid_reserved_final
PanelBackendReservationApp.refresh_check_price_grid = _refresh_check_price_grid_reserved_final
PanelBackendReservationApp.refresh_admin_grid = _refresh_admin_grid_reserved_final


if __name__ == "__main__":
    setup_file_logging()
    PanelBackendReservationApp().run()
    

# ---- Tulipan brand patch ----
def _refresh_header_right_info_tulipan(self, *_args):
    title = str(self._screen_header_title() or "").strip()
    self.header_right_title = title
    self.header_right_brand = "Cvetličarna Tulipan" if title else ""

base.PanelApp._refresh_header_right_info = _refresh_header_right_info_tulipan
PanelBackendReservationApp._refresh_header_right_info = _refresh_header_right_info_tulipan
