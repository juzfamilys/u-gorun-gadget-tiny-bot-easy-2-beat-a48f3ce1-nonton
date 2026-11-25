"""Tiny dip-buying bot for make.fun bars-replay/v1 (pure Python stdlib).

Strategy: two-tier oversold mean reversion, long only, across the Solana
hourly universe. The dev window falls ~9% peak to trough, so trend
following buys dips in a falling market and loses; the winning move here
is to buy washouts and sell the bounce quickly, then sit in cash.

  * Tier A (deep washout): RSI(14) <= 30 AND the symbol is down >= 3%
    over the last 6 bars. Bigger size (25% of equity), take profit +3%,
    hard stop -3%.
  * Tier B (mild oversold): RSI(14) <= 35 AND the symbol is down >= 1.5%
    over the last 6 bars. Smaller size (20% of equity), take profit
    +1.5%, hard stop -2.5%.
  * Every position also exits after 24 bars held (a bounce that never
    comes is not chased), and a symbol just exited rests 6 bars before
    it can be bought again, so one falling knife is not re-bought
    bar after bar.
  * At most 3 positions at once; tier A candidates first, then tier B,
    most oversold (lowest RSI) first. No new entries while warming up.
  * Deterministic: no randomness, no clock, no environment reads. All
    state is rebuilt from the bar stream, so two runs agree byte for byte.

Orders decided on bar k fill at bar k+1's open, so entries are sized
with the pool impact in mind (small slices, liquid symbols first).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

RSI_N = 14        # RSI length
DROP_N = 6        # washout lookback (bars)
MAX_POS = 3       # most symbols held at once
MAX_SLIPPAGE_BPS = 200   # 2% — wide enough for hourly moves, tight vs wild gaps
HOLD_BARS = 24    # max bars a position is held
COOLDOWN_BARS = 6  # bars a symbol rests after an exit before re-entry

# Tier A: deep washout. Tier B: mild oversold.
RSI_A = 30
DROP_A_BPS = 300   # -3% over DROP_N bars
WEIGHT_A_BPS = 2500
TP_A_BPS = 300     # +3%
SL_A_BPS = 300     # -3%
RSI_B = 35
DROP_B_BPS = 150   # -1.5% over DROP_N bars
WEIGHT_B_BPS = 2000
TP_B_BPS = 150     # +1.5%
SL_B_BPS = 250     # -2.5%

# Universe order, as the kit's manifest lists it; ties keep this order.
UNIVERSE = ("SOL", "WETH", "JITOSOL", "RAY", "WIF", "POPCAT", "FARTCOIN")


class Sym:
    """Per-symbol rolling state, rebuilt from closes as they arrive."""

    def __init__(self):
        self.closes = []
        self.rsi_gain = 0.0
        self.rsi_loss = 0.0
        self.rsi = None
        self.entry = None   # entry close (scaled int) of the open position
        self.peak = None    # highest close since entry
        self.age = 0        # bars since the entry decision
        self.cool = 0       # bars left before this symbol may be bought again
        self.tp_bps = 0     # take-profit of the open position, by tier
        self.sl_bps = 0     # hard stop of the open position, by tier

    def update(self, close):
        prev = self.closes[-1] if self.closes else None
        self.closes.append(close)
        if prev is not None:
            change = (close - prev) / prev if prev else 0.0
            gain = max(change, 0.0)
            loss = max(-change, 0.0)
            if len(self.closes) <= RSI_N + 1:
                self.rsi_gain += gain
                self.rsi_loss += loss
                if len(self.closes) == RSI_N + 1:
                    self.rsi_gain /= RSI_N
                    self.rsi_loss /= RSI_N
            else:
                self.rsi_gain = (self.rsi_gain * (RSI_N - 1) + gain) / RSI_N
                self.rsi_loss = (self.rsi_loss * (RSI_N - 1) + loss) / RSI_N
            if self.rsi_loss == 0.0:
                self.rsi = 100.0 if self.rsi_gain > 0 else 50.0
            else:
                self.rsi = 100.0 - 100.0 / (1.0 + self.rsi_gain / self.rsi_loss)
        if self.entry is not None:
            self.age += 1
            if self.peak is None or close > self.peak:
                self.peak = close
        if self.cool > 0:
            self.cool -= 1

    def drop_bps(self):
        """Trailing DROP_N-bar return, in bps (negative = fell)."""
        closes = self.closes
        if len(closes) <= DROP_N:
            return None
        return int((closes[-1] / closes[-DROP_N - 1] - 1.0) * 10_000)

    def tier(self):
        """Which entry tier this symbol qualifies for now, or None."""
        if self.rsi is None or self.cool > 0:
            return None
        drop = self.drop_bps()
        if drop is None:
            return None
        if self.rsi <= RSI_A and drop <= -DROP_A_BPS:
            return 0
        if self.rsi <= RSI_B and drop <= -DROP_B_BPS:
            return 1
        return None


BOOK = {sym: Sym() for sym in UNIVERSE}
TIER_PARAMS = (
    (WEIGHT_A_BPS, TP_A_BPS, SL_A_BPS),
    (WEIGHT_B_BPS, TP_B_BPS, SL_B_BPS),
)


def on_batch(state, batch):
    for bar in batch["bars"]:
        BOOK[bar["symbol"]].update(int(bar["c"]))
    if state.warming_up:
        # Warm-up bars build history only; every order here is rejected.
        return []

    orders = []
    held = set(state.positions)

    # --- exits first: sells free cash for the next bar's buys ---
    for symbol in sorted(held):
        sym = BOOK[symbol]
        holding = state.positions[symbol]
        close = state.close(symbol)
        if close is None or sym.entry is None:
            continue
        exit_now = False
        if close * 10_000 >= sym.entry * (10_000 + sym.tp_bps):
            exit_now = True
        elif close * 10_000 <= sym.entry * (10_000 - sym.sl_bps):
            exit_now = True
        elif sym.age >= HOLD_BARS:
            exit_now = True
        if exit_now:
            orders.append(
                {"side": "sell", "symbol": symbol, "qty": holding.qty,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}
            )
            sym.entry = None
            sym.peak = None
            sym.age = 0
            sym.cool = COOLDOWN_BARS

    # --- entries: tier A washouts first, then tier B, most oversold first ---
    free_slots = MAX_POS - (len(held) - len([o for o in orders if o["side"] == "sell"]))
    if free_slots > 0:
        candidates = []
        for symbol in UNIVERSE:
            if symbol in held:
                continue
            tier = BOOK[symbol].tier()
            if tier is None:
                continue
            candidates.append((tier, BOOK[symbol].rsi, symbol))
        # Deep washouts first, then the most oversold; ties keep universe order.
        candidates.sort(key=lambda c: (c[0], c[1], UNIVERSE.index(c[2])))
        cash_left = state.cash
        for tier, _, symbol in candidates[:free_slots]:
            weight_bps, tp_bps, sl_bps = TIER_PARAMS[tier]
            room = state.weight_room(symbol)
            if room <= 0:
                continue
            notional = state.equity * weight_bps // 10_000
            notional = min(notional, room, cash_left)
            if notional < int(state.hello["fees"]["min_notional_quote_units"]):
                continue
            orders.append(
                {"side": "buy", "symbol": symbol, "notional_quote_units": int(notional),
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}
            )
            cash_left -= notional
            sym = BOOK[symbol]
            sym.entry = state.close(symbol)
            sym.peak = state.close(symbol)
            sym.age = 0
            sym.tp_bps = tp_bps
            sym.sl_bps = sl_bps

    return orders


makefun_bars.run(on_batch)
