"""
One-shot verifier for the Crypto.com API credentials.

Run after refreshing CRYPTOCOM_API_KEY / CRYPTOCOM_SECRET in .env:

    python -m infra.verify_cryptocom_auth

Prints PASS or a clear-text diagnostic. Never prints the key or secret.
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    try:
        from trading.exchange import get_account_balance, get_portfolio_value_usd
        balances = get_account_balance()
    except Exception as exc:
        msg = str(exc)
        print(f"❌ FAIL: {msg[:200]}")
        if "401" in msg:
            print()
            print("   401 Unauthorized — the API key/secret pair is invalid.")
            print("   Steps to fix:")
            print("     1. crypto.com/exchange → Settings → API Keys")
            print("     2. Delete the old key (revoke).")
            print("     3. Create a new key with permissions: read + trade (NOT withdraw).")
            print("     4. Paste the new key into CRYPTOCOM_API_KEY in .env.")
            print("     5. Paste the new secret into CRYPTOCOM_SECRET in .env.")
            print("     6. Restart the bot (kill + relaunch python -m content.receiver).")
            print("     7. Rerun this script.")
        elif "must be set in .env" in msg:
            print()
            print("   One of CRYPTOCOM_API_KEY / CRYPTOCOM_SECRET is empty in .env.")
        return 1

    nonzero = {c: a for c, a in balances.items() if a["total"] > 0}
    usd = get_portfolio_value_usd(balances)
    print(f"✅ PASS — Crypto.com auth works.")
    print(f"   currencies with balance: {list(nonzero.keys()) or '(none)'}")
    for cur, amts in nonzero.items():
        print(f"     {cur}:  total={amts['total']:.6f}  available={amts['available']:.6f}")
    print(f"   portfolio USD value: ${usd:.2f}")
    print()
    print(f"   Suggested STARTING_BALANCE_USD = {usd:.2f}")
    print(f"   (current breaker baseline; update in .env if you want the breaker")
    print(f"    to measure drawdown from this snapshot.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
