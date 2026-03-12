# Dhan VWAP 2m Strategy — Balfund

NIFTY ATM options selling strategy using 2-minute VWAP on Dhan API.

## Strategy Logic

1. Locks NIFTY ATM strike from 09:16 REST candle close (rounded to nearest 50)
2. Seeds VWAP history from 09:15 via REST 1m data
3. Streams live TICKER + FULL packets via Dhan WebSocket
4. Builds broker-aligned 2-minute candles (09:15, 09:17, 09:19 … anchor)
5. Computes tick-level session VWAP from FULL packet volume deltas
6. **Trigger**: 2m candle closes below VWAP
7. **Confirmation**: next candle closes below trigger's Low
8. **Entry**: SELL at next candle's Open
9. **SL**: trigger candle High (clamped 10–20 pts above entry)
10. Square off at 15:15 IST

Runs both CE and PE legs independently. Max 3 trades per leg. No new entries after 14:45.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in .env
python gui.py
```

## EXE (Windows)

The GitHub Actions workflow automatically builds `DhanVWAPTrader.exe` on every push to `main`.

**Download:** Go to **Actions → latest run → Artifacts → DhanVWAPTrader-Windows**

## Repo Structure

```
dhan-vwap-strategy/
├── .env.example
├── .github/workflows/build.yml   ← auto builds EXE
├── README.md
├── requirements.txt
├── DhanVWAPTrader.spec            ← PyInstaller config
├── gui.py                         ← EXE entry point (CustomTkinter UI)
├── main.py                        ← App class / headless entry point
├── candle_engine.py               ← 1m/2m candle builder + SessionVWAP
├── strategy_engine.py             ← trigger / confirmation / entry logic
├── market_feed.py                 ← Dhan WebSocket feed
├── instrument_resolver.py         ← ATM CE/PE lookup from instrument master
├── dhan_api.py                    ← Dhan REST API wrapper
├── dhan_token_manager.py          ← TOTP-based token auto-refresh
├── config.py                      ← SETTINGS dataclass
├── executors.py                   ← PaperExecutor / LiveExecutor
├── time_utils.py                  ← IST helpers, broker-aligned 2m buckets
├── logger_setup.py
└── state_store.py
```

## Token Refresh

```bash
# One-shot
python dhan_token_manager.py

# Daemon (auto-refreshes daily at 8 AM)
python dhan_token_manager.py --daemon
```
