"""
gui.py  —  Dhan VWAP 2m Strategy  |  CustomTkinter UI
Entry point for the Windows EXE build.

Credentials: Client ID + 4-digit PIN + TOTP Secret.
Token is auto-generated via dhan_token_manager on every Start click.
No manual token pasting required — no corruption possible.
"""
from __future__ import annotations

import math
import queue
import threading
import logging
from datetime import datetime
from typing import Optional

import customtkinter as ctk

import os
from dotenv import load_dotenv, set_key
load_dotenv()

from main import App
from time_utils import IST
from dhan_token_manager import generate_token_via_totp


# ─── colour palette ───────────────────────────────────────────────────────────
C_BG       = "#1a1a2e"
C_PANEL    = "#16213e"
C_ACCENT   = "#0f3460"
C_GREEN    = "#00d2a0"
C_RED      = "#ff4757"
C_YELLOW   = "#ffa502"
C_WHITE    = "#e0e0e0"
C_DIM      = "#8888aa"
C_BORDER   = "#2a2a4a"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


# ─── queue-based log handler ──────────────────────────────────────────────────

class QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.log_queue.put_nowait(self.format(record))
        except Exception:
            pass


# ─── utility ──────────────────────────────────────────────────────────────────

def _fmt(v, prec=2, fallback="—") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return fallback
    return f"{v:,.{prec}f}"

def _color(v, ref) -> str:
    if v is None or ref is None: return C_WHITE
    return C_GREEN if float(v) >= float(ref) else C_RED

def _clean(s: str) -> str:
    """Strip and remove any newlines — safe for use as HTTP header value."""
    return s.strip().replace("\n", "").replace("\r", "")


# ─── reusable leg card ────────────────────────────────────────────────────────

class LegCard(ctk.CTkFrame):
    def __init__(self, master, label: str, **kwargs):
        super().__init__(master, fg_color=C_PANEL, corner_radius=12,
                         border_width=1, border_color=C_BORDER, **kwargs)
        self._label = label
        self._build()

    def _build(self):
        pad = {"padx": 14, "pady": 4}

        hdr = ctk.CTkFrame(self, fg_color=C_ACCENT, corner_radius=8)
        hdr.pack(fill="x", padx=10, pady=(10, 4))
        self._lbl_title = ctk.CTkLabel(hdr, text=self._label,
                                        font=ctk.CTkFont(size=13, weight="bold"),
                                        text_color=C_WHITE)
        self._lbl_title.pack(side="left", padx=12, pady=6)
        self._lbl_symbol = ctk.CTkLabel(hdr, text="—",
                                         font=ctk.CTkFont(size=11),
                                         text_color=C_DIM)
        self._lbl_symbol.pack(side="right", padx=12, pady=6)

        self._candle_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._candle_frame.pack(fill="x", **pad)
        for col, key in enumerate(["Open", "High", "Low", "Close"]):
            ctk.CTkLabel(self._candle_frame, text=key,
                         font=ctk.CTkFont(size=10),
                         text_color=C_DIM).grid(row=0, column=col, padx=8)
        self._c_open  = self._val_lbl(self._candle_frame, 1, 0)
        self._c_high  = self._val_lbl(self._candle_frame, 1, 1, C_GREEN)
        self._c_low   = self._val_lbl(self._candle_frame, 1, 2, C_RED)
        self._c_close = self._val_lbl(self._candle_frame, 1, 3)

        vrow = ctk.CTkFrame(self, fg_color="transparent")
        vrow.pack(fill="x", **pad)
        ctk.CTkLabel(vrow, text="VWAP", font=ctk.CTkFont(size=10),
                     text_color=C_DIM).pack(side="left")
        self._lbl_vwap = ctk.CTkLabel(vrow, text="—",
                                       font=ctk.CTkFont(size=13, weight="bold"),
                                       text_color=C_YELLOW)
        self._lbl_vwap.pack(side="left", padx=8)
        self._lbl_ctime = ctk.CTkLabel(vrow, text="",
                                        font=ctk.CTkFont(size=10),
                                        text_color=C_DIM)
        self._lbl_ctime.pack(side="right")

        srow = ctk.CTkFrame(self, fg_color="transparent")
        srow.pack(fill="x", **pad)
        ctk.CTkLabel(srow, text="Signal", font=ctk.CTkFont(size=10),
                     text_color=C_DIM).pack(side="left")
        self._lbl_signal = ctk.CTkLabel(srow, text="IDLE",
                                         font=ctk.CTkFont(size=12, weight="bold"),
                                         text_color=C_DIM)
        self._lbl_signal.pack(side="left", padx=8)
        self._lbl_trades = ctk.CTkLabel(srow, text="Trades: 0",
                                         font=ctk.CTkFont(size=10),
                                         text_color=C_DIM)
        self._lbl_trades.pack(side="right")

        self._pos_frame = ctk.CTkFrame(self, fg_color=C_ACCENT, corner_radius=8)
        self._pos_frame.pack(fill="x", padx=10, pady=(4, 10))
        self._lbl_pos = ctk.CTkLabel(self._pos_frame, text="FLAT",
                                      font=ctk.CTkFont(size=12, weight="bold"),
                                      text_color=C_DIM)
        self._lbl_pos.pack(pady=6)

    @staticmethod
    def _val_lbl(parent, row, col, color=C_WHITE):
        lbl = ctk.CTkLabel(parent, text="—",
                           font=ctk.CTkFont(size=13, weight="bold"),
                           text_color=color)
        lbl.grid(row=row, column=col, padx=8, pady=2)
        return lbl

    def update(self, snap: dict):
        if not snap:
            return
        self._lbl_symbol.configure(text=snap.get("symbol", "—")[-20:])
        c = snap.get("candle")
        if c:
            self._c_open.configure(text=_fmt(c["open"]))
            self._c_high.configure(text=_fmt(c["high"]))
            self._c_low.configure(text=_fmt(c["low"]))
            self._c_close.configure(text=_fmt(c["close"]),
                                    text_color=_color(c["close"], c["open"]))
            ts = datetime.fromtimestamp(c["bucket"], IST).strftime("%H:%M")
            self._lbl_ctime.configure(text=f"candle {ts}")
        vwap = snap.get("vwap")
        self._lbl_vwap.configure(text=_fmt(vwap))

        if snap.get("pending"):
            sig, sig_col = "ENTRY PENDING ▼", C_YELLOW
        elif snap.get("trigger"):
            sig, sig_col = "TRIGGER SET ◆", C_YELLOW
        else:
            sig, sig_col = "IDLE", C_DIM
        self._lbl_signal.configure(text=sig, text_color=sig_col)
        self._lbl_trades.configure(text=f"Trades: {snap.get('trades', 0)}/3")

        pos = snap.get("position")
        if pos and pos.get("is_open"):
            entry     = pos["entry"]
            sl        = pos["sl"]
            close_val = c["close"] if c else entry
            pnl       = entry - close_val
            pnl_col   = C_GREEN if pnl >= 0 else C_RED
            txt = (f"SHORT  Entry={_fmt(entry)}  SL={_fmt(sl)}\n"
                   f"Live P&L  {_fmt(pnl)}  pts")
            self._lbl_pos.configure(text=txt, text_color=pnl_col)
        elif pos and not pos.get("is_open"):
            exit_p  = pos.get("exit") or 0
            entry   = pos.get("entry") or exit_p
            pnl     = entry - exit_p
            pnl_col = C_GREEN if pnl >= 0 else C_RED
            txt = f"CLOSED ({pos.get('reason','')})  P&L {_fmt(pnl)} pts"
            self._lbl_pos.configure(text=txt, text_color=pnl_col)
        else:
            self._lbl_pos.configure(text="FLAT", text_color=C_DIM)


# ─── main window ──────────────────────────────────────────────────────────────

class StrategyGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Dhan VWAP 2m Strategy — Balfund")
        self.geometry("960x820")
        self.resizable(True, True)
        self.configure(fg_color=C_BG)

        self._app:    Optional[App]              = None
        self._thread: Optional[threading.Thread] = None
        self._log_q:  queue.Queue                = queue.Queue(maxsize=500)

        self._attach_log_handler()
        self._build_ui()
        self._poll()

    # ── log handler ───────────────────────────────────────────

    def _attach_log_handler(self):
        h = QueueHandler(self._log_q)
        h.setLevel(logging.INFO)
        logging.getLogger().addHandler(h)
        logging.getLogger().setLevel(logging.INFO)

    # ── UI layout ─────────────────────────────────────────────

    def _build_ui(self):
        # === Top bar ===
        top = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=0, height=52)
        top.pack(fill="x")
        top.pack_propagate(False)
        ctk.CTkLabel(top, text="  Dhan VWAP 2m Strategy",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=C_WHITE).pack(side="left", padx=4)
        self._mode_var = ctk.StringVar(value=os.getenv("TRADING_MODE", "PAPER").upper())
        ctk.CTkSegmentedButton(
            top, values=["PAPER", "LIVE"],
            variable=self._mode_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            width=160,
        ).pack(side="right", padx=12, pady=8)
        ctk.CTkLabel(top, text="Mode:", font=ctk.CTkFont(size=11),
                     text_color=C_DIM).pack(side="right")

        # === Credentials panel ===
        cred_outer = ctk.CTkFrame(self, fg_color=C_ACCENT, corner_radius=0)
        cred_outer.pack(fill="x")

        cred = ctk.CTkFrame(cred_outer, fg_color="transparent")
        cred.pack(fill="x", padx=12, pady=6)

        # Row 1: Client ID + PIN
        row1 = ctk.CTkFrame(cred, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(row1, text="Client ID:", font=ctk.CTkFont(size=11),
                     text_color=C_DIM).pack(side="left")
        self._ent_cid = ctk.CTkEntry(row1, width=140, font=ctk.CTkFont(size=11),
                                      placeholder_text="e.g. 1106958355")
        self._ent_cid.insert(0, os.getenv("DHAN_CLIENT_ID", ""))
        self._ent_cid.pack(side="left", padx=(4, 20))

        ctk.CTkLabel(row1, text="PIN (4-digit):", font=ctk.CTkFont(size=11),
                     text_color=C_DIM).pack(side="left")
        self._ent_pin = ctk.CTkEntry(row1, width=80, font=ctk.CTkFont(size=11),
                                      show="*", placeholder_text="PIN")
        self._ent_pin.insert(0, os.getenv("DHAN_PIN", ""))
        self._ent_pin.pack(side="left", padx=(4, 20))

        # Row 2: TOTP Secret + buttons
        row2 = ctk.CTkFrame(cred, fg_color="transparent")
        row2.pack(fill="x")

        ctk.CTkLabel(row2, text="TOTP Secret:", font=ctk.CTkFont(size=11),
                     text_color=C_DIM).pack(side="left")
        self._ent_totp = ctk.CTkEntry(row2, width=300, font=ctk.CTkFont(size=11),
                                       show="*",
                                       placeholder_text="From web.dhan.co → Profile → API Access")
        self._ent_totp.insert(0, os.getenv("DHAN_TOTP_SECRET", ""))
        self._ent_totp.pack(side="left", padx=(4, 12))

        self._btn_save = ctk.CTkButton(
            row2, text="💾 Save", width=80,
            font=ctk.CTkFont(size=11),
            fg_color=C_ACCENT, hover_color="#1a4a80",
            command=self._save_creds,
        )
        self._btn_save.pack(side="left", padx=(0, 6))

        self._btn_gen = ctk.CTkButton(
            row2, text="🔑 Generate Token",
            width=150, font=ctk.CTkFont(size=11),
            fg_color="#2a5a30", hover_color="#1e4424",
            command=self._generate_token_manual,
        )
        self._btn_gen.pack(side="left", padx=(0, 6))

        self._lbl_token_status = ctk.CTkLabel(
            row2, text="Token: not generated",
            font=ctk.CTkFont(size=10), text_color=C_DIM,
        )
        self._lbl_token_status.pack(side="left", padx=8)

        # === Status bar ===
        sbar = ctk.CTkFrame(self, fg_color="#0d0d1f", corner_radius=0, height=30)
        sbar.pack(fill="x")
        sbar.pack_propagate(False)
        self._lbl_status = ctk.CTkLabel(sbar, text="● Idle",
                                         font=ctk.CTkFont(size=11),
                                         text_color=C_DIM)
        self._lbl_status.pack(side="left", padx=12)
        self._lbl_atm = ctk.CTkLabel(sbar, text="",
                                      font=ctk.CTkFont(size=11),
                                      text_color=C_YELLOW)
        self._lbl_atm.pack(side="left", padx=20)
        self._lbl_clock = ctk.CTkLabel(sbar, text="",
                                        font=ctk.CTkFont(size=11),
                                        text_color=C_DIM)
        self._lbl_clock.pack(side="right", padx=12)

        # === Control buttons ===
        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.pack(fill="x", padx=16, pady=8)

        self._btn_start = ctk.CTkButton(
            ctrl, text="▶  Start Strategy", width=180, height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=C_GREEN, hover_color="#00b386", text_color="#000000",
            command=self._start,
        )
        self._btn_start.pack(side="left", padx=(0, 8))

        self._btn_stop = ctk.CTkButton(
            ctrl, text="■  Stop / Square Off", width=180, height=38,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=C_RED, hover_color="#cc3344",
            command=self._stop, state="disabled",
        )
        self._btn_stop.pack(side="left", padx=8)

        self._lbl_mode_badge = ctk.CTkLabel(
            ctrl, text="PAPER MODE",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=C_YELLOW,
        )
        self._lbl_mode_badge.pack(side="right")

        # === Leg cards ===
        legs = ctk.CTkFrame(self, fg_color="transparent")
        legs.pack(fill="both", expand=False, padx=16, pady=4)
        legs.columnconfigure(0, weight=1)
        legs.columnconfigure(1, weight=1)
        self._card_ce = LegCard(legs, "CALL (CE)")
        self._card_ce.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._card_pe = LegCard(legs, "PUT (PE)")
        self._card_pe.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        # === Log area ===
        log_frame = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=12,
                                  border_width=1, border_color=C_BORDER)
        log_frame.pack(fill="both", expand=True, padx=16, pady=(4, 12))
        ctk.CTkLabel(log_frame, text="Live Log",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=C_DIM).pack(anchor="w", padx=12, pady=(6, 0))
        self._log_box = ctk.CTkTextbox(
            log_frame,
            font=ctk.CTkFont(family="Courier", size=10),
            fg_color="#0d0d1f", text_color=C_WHITE,
            activate_scrollbars=True,
        )
        self._log_box.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self._log_box.configure(state="disabled")

    # ── credentials ───────────────────────────────────────────

    def _get_creds(self):
        """Return (cid, pin, totp) stripped, or None if any are missing."""
        cid  = _clean(self._ent_cid.get())
        pin  = _clean(self._ent_pin.get())
        totp = _clean(self._ent_totp.get())
        if not cid or not pin or not totp:
            return None, None, None
        return cid, pin, totp

    def _save_creds(self):
        cid, pin, totp = self._get_creds()
        if not cid or not pin or not totp:
            self._append_log("⚠  Client ID, PIN and TOTP Secret are all required.")
            return
        env_file = ".env"
        set_key(env_file, "DHAN_CLIENT_ID",    cid)
        set_key(env_file, "DHAN_PIN",          pin)
        set_key(env_file, "DHAN_TOTP_SECRET",  totp)
        set_key(env_file, "TRADING_MODE",      self._mode_var.get())
        os.environ["DHAN_CLIENT_ID"]   = cid
        os.environ["DHAN_PIN"]         = pin
        os.environ["DHAN_TOTP_SECRET"] = totp
        os.environ["TRADING_MODE"]     = self._mode_var.get()
        self._append_log(f"Credentials saved  (Client ID: {cid})")

    def _generate_token(self, cid: str, pin: str, totp: str) -> Optional[str]:
        """
        Call dhan_token_manager to generate a fresh token.
        Returns the token string on success, None on failure.
        Runs on the calling thread — wrap in a thread for non-blocking use.
        """
        self._append_log("Generating token via TOTP...")
        result = generate_token_via_totp(cid, pin, totp)
        if result["success"]:
            tok = _clean(result["access_token"])
            exp = result.get("expiry", "")
            self._append_log(f"Token generated OK. Expires: {exp}")
            # Persist to .env so next launch loads it
            set_key(".env", "DHAN_ACCESS_TOKEN", tok)
            os.environ["DHAN_ACCESS_TOKEN"] = tok
            self.after(0, lambda: self._lbl_token_status.configure(
                text=f"Token OK  (exp {exp[:10]})", text_color=C_GREEN))
            return tok
        else:
            err = result.get("error", "Unknown error")
            self._append_log(f"Token generation FAILED: {err}")
            self.after(0, lambda: self._lbl_token_status.configure(
                text="Token FAILED", text_color=C_RED))
            return None

    def _generate_token_manual(self):
        """Manual 'Generate Token' button — runs in background thread."""
        cid, pin, totp = self._get_creds()
        if not cid or not pin or not totp:
            self._append_log("⚠  Fill in Client ID, PIN and TOTP Secret first.")
            return
        self._btn_gen.configure(state="disabled", text="Generating...")
        def _task():
            self._generate_token(cid, pin, totp)
            self.after(0, lambda: self._btn_gen.configure(
                state="normal", text="🔑 Generate Token"))
        threading.Thread(target=_task, daemon=True).start()

    # ── start / stop ──────────────────────────────────────────

    def _start(self):
        if self._thread and self._thread.is_alive():
            return

        cid, pin, totp = self._get_creds()
        if not cid or not pin or not totp:
            self._append_log("⚠  Fill in Client ID, PIN and TOTP Secret before starting.")
            return

        self._btn_start.configure(state="disabled", text="Generating token...")

        def _task():
            # Always generate a fresh token on Start — never rely on a stale one
            tok = self._generate_token(cid, pin, totp)
            if tok is None:
                self.after(0, lambda: self._btn_start.configure(
                    state="normal", text="▶  Start Strategy"))
                return

            # Patch SETTINGS singleton in-place — all modules share this object
            import config
            config.SETTINGS.dhan_client_id    = cid
            config.SETTINGS.dhan_access_token = tok
            config.SETTINGS.mode              = self._mode_var.get()
            config.SETTINGS.market_feed_ws_url = (
                f"wss://api-feed.dhan.co?version=2"
                f"&token={tok}&clientId={cid}&authType=2"
            )
            os.environ["DHAN_CLIENT_ID"]    = cid
            os.environ["DHAN_ACCESS_TOKEN"] = tok
            os.environ["TRADING_MODE"]      = self._mode_var.get()

            self._app = App()
            # Belt-and-braces: update live session headers too
            self._app.api.session.headers.update({
                "access-token": tok,
                "client-id":    cid,
                "dhanClientId": cid,
            })

            self.after(0, self._on_started)
            self._app.run()
            self.after(0, self._on_strategy_done)

        self._thread = threading.Thread(target=_task, daemon=True)
        self._thread.start()

    def _on_started(self):
        self._btn_start.configure(state="disabled", text="▶  Start Strategy")
        self._btn_stop.configure(state="normal")
        mode = self._mode_var.get()
        badge     = "PAPER MODE" if mode == "PAPER" else "⚡ LIVE MODE"
        badge_col = C_YELLOW     if mode == "PAPER" else C_RED
        self._lbl_mode_badge.configure(text=badge, text_color=badge_col)

    def _on_strategy_done(self):
        self._btn_start.configure(state="normal", text="▶  Start Strategy")
        self._btn_stop.configure(state="disabled")

    def _stop(self):
        if self._app:
            threading.Thread(target=self._app.stop, daemon=True).start()
        self._btn_start.configure(state="normal", text="▶  Start Strategy")
        self._btn_stop.configure(state="disabled")

    # ── polling loop ──────────────────────────────────────────

    def _poll(self):
        self._lbl_clock.configure(
            text=datetime.now(IST).strftime("%d %b %Y  %H:%M:%S IST"))

        # Drain log queue
        msgs = []
        while True:
            try:    msgs.append(self._log_q.get_nowait())
            except queue.Empty: break
        if msgs:
            self._log_box.configure(state="normal")
            for m in msgs:
                self._log_box.insert("end", m + "\n")
            lines = self._log_box.get("1.0", "end").splitlines()
            if len(lines) > 2000:
                self._log_box.delete("1.0", f"{len(lines) - 2000}.0")
            self._log_box.see("end")
            self._log_box.configure(state="disabled")

        # Strategy status
        if self._app and self._thread and self._thread.is_alive():
            snap = self._app.get_status()
            self._lbl_status.configure(
                text=f"● {snap['status_msg']}", text_color=C_GREEN)
            if snap.get("nifty_ref"):
                atm = round(snap["nifty_ref"] / 50) * 50
                self._lbl_atm.configure(
                    text=f"ATM {atm}  |  Ref {snap['nifty_ref']:.2f}")
            self._card_ce.update(snap.get("ce", {}))
            self._card_pe.update(snap.get("pe", {}))
        elif not (self._thread and self._thread.is_alive()):
            self._lbl_status.configure(text="● Idle", text_color=C_DIM)

        self.after(1000, self._poll)

    def _append_log(self, msg: str):
        self._log_box.configure(state="normal")
        self._log_box.insert("end", msg + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")


# ─── entry point ──────────────────────────────────────────────────────────────

def main():
    StrategyGUI().mainloop()

if __name__ == "__main__":
    main()
