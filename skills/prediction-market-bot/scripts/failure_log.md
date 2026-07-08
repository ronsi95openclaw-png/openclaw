# Prediction Market Failure Log

Use this file to capture lessons after every trade, especially losses.

Format each entry as:

## YYYY-MM-DD HH:MM UTC | MARKET_ID | outcome
- Profit/loss: 
- Classification: prediction / execution / timing / information / unknown
- Summary: 
- Actions: 

Example:

## 2026-04-16 12:30 UTC | kalshi-1234 | loss
- Profit/loss: -$120.00
- Classification: execution
- Summary: Limit order never filled and market moved against us.
- Actions: Reduce bid aggressiveness, implement fill monitoring, run paper simulation.
