# ---------------------------------------------------------
# verify_ton.py – SIMULATION MODE (no blockchain required)
# ---------------------------------------------------------

def verify_ton_transaction(tx_hash: str, usd_amount_expected: float):
    """
    SIMULATED TON PAYMENT VERIFICATION
    Always returns success if tx_hash begins with "SIMTX-"
    """

    # user must send a simulated tx hash
    if not tx_hash or not str(tx_hash).startswith("SIMTX-"):
        return False, "invalid_or_missing_simulated_tx"

    # Validate expected amount
    try:
        usd_amount_expected = float(usd_amount_expected)
    except:
        return False, "invalid_amount"

    if usd_amount_expected < 20.0:
        return False, "min_deposit_is_20"

    # Success result
    return True, {
        "from": "SIM_SOURCE_WALLET",
        "to": "SIM_TREASURY_WALLET",
        "usd_value": usd_amount_expected,
        "simulated": True
    }
