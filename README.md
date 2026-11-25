# Tiny Dip-Buying Bot — Beat Buy-and-Hold on Solana

[![make.fun](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1.svg)](https://make.fun/competitions/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)

A tiny long-only dip-buying bot for the make.fun `agent-fun/bars-replay/v1`
hourly Solana bars replay. Pure Python standard library, deterministic, no
network, no clock, no environment reads.

## Strategy

The dev window falls about 9% peak to trough, so trend following buys dips
in a falling market and loses. This bot does the opposite: it buys washouts
and sells the bounce quickly, then sits in cash.

- **Tier A — deep washout:** enter when RSI(14) <= 30 **and** the symbol is
  down >= 3% over the last 6 bars. Size 25% of equity, take profit +3%,
  hard stop -3%.
- **Tier B — mild oversold:** enter when RSI(14) <= 35 **and** the symbol is
  down >= 1.5% over the last 6 bars. Size 20% of equity, take profit +1.5%,
  hard stop -2.5%.
- **Time exit:** every position also exits after 24 bars held — a bounce
  that never comes is not chased. A symbol just exited rests 6 bars before
  it can be bought again, so one falling knife is not re-bought bar after bar.
- **Discipline:** at most 3 positions at once; tier A candidates first,
  then tier B, most oversold (lowest RSI) first. Exits are sent before
  entries each bar; positions flatten automatically at the end of the window.
- **Costs kept low:** 2% max slippage, entries only on genuine oversold
  signals, no over-trading.

The parameters are round, structural choices (RSI 14, 6-bar washout, 24-bar
max hold), checked for stability on three rolling 72-bar slices of the dev
window (each slice positive, 7–12 trades) rather than tuned to a single
number.

## Dev score (grade-local, process driver)

- **Net PnL +367.90 USDC** (+3.68% on 10,000 USDC capital)
- 20 trades over 6 symbols, win rate 70%, max drawdown 2.73%, 0 rejected
- Fees 47.30 USDC; at 2x fees net PnL +320.30 USDC
- vs hold-cash 0.00 USDC and the best house baseline macd-cci −20.22 USDC

The previous version (v1, long-only EMA 8/21 crossover + RSI filter) scored
−168.35 USDC with a 9% win rate on the same window; it is replaced by this
strategy.

## Files

- `main.py` — the bot (entrypoint; the grader runs `python3 -P -s /bot/main.py`)
- `vendor/makefun_bars.py` — the kit-provided protocol shim, vendored as the
  baselines do
- `LICENSE` — MIT (written by `makefun init`)

## Grade locally

From the kit directory:

```sh
node cli/makefun.mjs grade-local --kit . \
  --bot ~/workspace/makefun/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1-nonton \
  --driver process --worktree
```

## License

MIT — see `LICENSE`.
