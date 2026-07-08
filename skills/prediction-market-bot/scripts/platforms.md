# Prediction Market Platforms

## Polymarket

- Polymarket is a crypto-native prediction market built on Polygon.
- Data is accessible via public discovery APIs and a private orderbook API.
- Authentication normally uses EIP-712 signed messages.
- Useful endpoints:
  - market discovery
  - orderbook and quote data
  - account balance and order placement

### Notes

- Use demo or smaller stake markets first.
- Watch for geo restrictions and regulatory warnings.
- Prefer limit orders to manage slippage.

## Kalshi

- Kalshi is a US-regulated prediction market exchange with REST APIs.
- It has a sandbox/demo environment for testing.
- API requests require header signing and authentication tokens.

### Notes

- Read the developer agreement before trading.
- Use the demo environment exclusively during development.
- Track API limits and error codes carefully.

## Unified wrapper guidance

A shared wrapper should support:
- market discovery and metadata
- current best bid/ask and implied probability
- order placement and cancellation
- position exposure and account balances

## Research sources

- News RSS feeds for event-specific updates
- Twitter/X for real-time sentiment
- Reddit for community consensus
- Regulatory filings and official announcements
- Historical trading data for calibration
