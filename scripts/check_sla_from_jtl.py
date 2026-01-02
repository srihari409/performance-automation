import csv
import math
import sys
import requests
import argparse
from collections import defaultdict
from datetime import datetime, timezone

def percentile(sorted_values, p: float):
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_values[int(k)])
    return float(sorted_values[f]) * (c - k) + float(sorted_values[c]) * (k - f)

def slack_post(token, channel, text):
    url = "https://slack.com/api/chat.postMessage"
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        data={"channel": channel, "text": text},
        timeout=30
    )
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"Slack post failed: {r.text}")

def compute_stats(elapsed_list, total, errors):
    if total == 0:
        return None
    arr = sorted(elapsed_list)
    avg = sum(arr) / len(arr) if arr else 0
    p95 = percentile(arr, 95)
    p99 = percentile(arr, 99)
    err_pct = (errors * 100.0) / total if total else 0.0
    return {"avg": avg, "p95": p95, "p99": p99, "err_pct": err_pct, "total": total, "errors": errors}

def get_breaches(stats, p95_limit, p99_limit, err_limit):
    b = []
    if stats["p95"] is not None and stats["p95"] > p95_limit:
        b.append(f"p95 {stats['p95']:.0f}ms > {p95_limit}ms")
    if stats["p99"] is not None and stats["p99"] > p99_limit:
        b.append(f"p99 {stats['p99']:.0f}ms > {p99_limit}ms")
    if stats["err_pct"] > err_limit:
        b.append(f"errors {stats['err_pct']:.2f}% > {err_limit:.2f}%")
    return b

def fmt_local(epoch_ms: int):
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")

def gha_notice(msg: str):
    print(f"::notice::{msg}")

def gha_warning(msg: str):
    print(f"::warning::{msg}")

def gha_error(msg: str):
    print(f"::error::{msg}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jtl_csv_path")
    parser.add_argument("slack_token")
    parser.add_argument("channel_id")
    parser.add_argument("test_name", nargs="?", default="Performance Test")
    parser.add_argument("--annotations", action="store_true", help="Emit GitHub Actions annotations")
    parser.add_argument("--fail-on-breach", action="store_true", help="Exit non-zero if SLA breaches")
    args = parser.parse_args()

    jtl_path = args.jtl_csv_path
    token = args.slack_token
    channel = args.channel_id
    test_name = args.test_name

    # ---- SLA RULES ----
    OVERALL_P95_MS = 250
    OVERALL_P99_MS = 500
    OVERALL_ERR_PCT = 1.0

    TXN_LABEL = "Transaction Controller_Home"
    TXN_P95_MS = 250
    TXN_P99_MS = 500
    TXN_ERR_PCT = 1.0

    TOP_N_LABELS = 5
    # -------------------

    overall_elapsed = []
    overall_total = 0
    overall_errors = 0

    txn_elapsed = []
    txn_total = 0
    txn_errors = 0

    elapsed_by_label = defaultdict(list)
    errors_by_label = defaultdict(lambda: {"total": 0, "errors": 0})

    min_ts = None
    max_ts = None

    with open(jtl_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label = (row.get("label") or "").strip()
            success = (row.get("success") or "").strip().lower() == "true"

            try:
                elapsed = int(float(row.get("elapsed") or 0))
            except Exception:
                elapsed = 0

            try:
                ts = int(float(row.get("timeStamp") or 0))
                if min_ts is None or ts < min_ts:
                    min_ts = ts
                if max_ts is None or ts > max_ts:
                    max_ts = ts
            except Exception:
                pass

            overall_total += 1
            overall_elapsed.append(elapsed)

            errors_by_label[label]["total"] += 1
            elapsed_by_label[label].append(elapsed)

            if not success:
                overall_errors += 1
                errors_by_label[label]["errors"] += 1

            if label == TXN_LABEL:
                txn_total += 1
                txn_elapsed.append(elapsed)
                if not success:
                    txn_errors += 1

    # --- ALWAYS emit at least one annotation when --annotations is enabled ---
    if args.annotations:
        gha_notice(f"SLA check started for: {test_name} (JTL={jtl_path})")

    if overall_total == 0:
        slack_post(token, channel, "⚠️ SLA Check: JTL is empty (0 samples).")
        if args.annotations:
            gha_warning("JTL is empty (0 samples).")
        return 0

    overall = compute_stats(overall_elapsed, overall_total, overall_errors)
    overall_breaches = get_breaches(overall, OVERALL_P95_MS, OVERALL_P99_MS, OVERALL_ERR_PCT)

    txn_breaches = []
    txn_text = f"\nℹ️ Transaction label not found in JTL: `{TXN_LABEL}`\n"
    txn = None
    if txn_total > 0:
        txn = compute_stats(txn_elapsed, txn_total, txn_errors)
        txn_breaches = get_breaches(txn, TXN_P95_MS, TXN_P99_MS, TXN_ERR_PCT)
        txn_text = (
            f"\n*Transaction:* `{TXN_LABEL}`\n"
            f"• samples={txn['total']} | errors={txn['errors']} ({txn['err_pct']:.2f}%)\n"
            f"• avg={txn['avg']:.0f}ms | p95={txn['p95']:.0f}ms | p99={txn['p99']:.0f}ms\n"
            f"• rules: p95<={TXN_P95_MS}ms, p99<={TXN_P99_MS}ms, errors<={TXN_ERR_PCT:.2f}%\n"
        )
        if txn_breaches:
            txn_text += "• breaches: " + ", ".join(txn_breaches) + "\n"

    # Window + throughput
    window_text = ""
    duration_s = None
    rps = None
    if min_ts is not None and max_ts is not None and max_ts > min_ts:
        duration_s = (max_ts - min_ts) / 1000.0
        rps = overall_total / duration_s if duration_s > 0 else None
        window_text = (
            f"*Window:* {fmt_local(min_ts)} → {fmt_local(max_ts)}\n"
            f"*Duration:* {duration_s:.0f}s | *Throughput:* {rps:.2f} req/s\n"
        )

    # Top slow labels
    label_rows = []
    for label, arr in elapsed_by_label.items():
        if not arr:
            continue
        arr_sorted = sorted(arr)
        lp95 = percentile(arr_sorted, 95) or 0
        lavg = sum(arr_sorted) / len(arr_sorted)
        e = errors_by_label[label]["errors"]
        t = errors_by_label[label]["total"]
        le = (e * 100.0 / t) if t else 0.0
        label_rows.append((lp95, lavg, le, t, label))

    label_rows.sort(reverse=True, key=lambda x: x[0])
    top_labels = label_rows[:TOP_N_LABELS]

    any_breach = bool(overall_breaches or txn_breaches)
    header = "🚨 *SLA BREACH DETECTED*" if any_breach else "✅ *SLA OK*"

    msg = (
        f"{header}\n"
        f"*Test:* {test_name}\n"
        f"{window_text}"
        f"*Overall:*\n"
        f"• samples={overall['total']} | errors={overall['errors']} ({overall['err_pct']:.2f}%)\n"
        f"• avg={overall['avg']:.0f}ms | p95={overall['p95']:.0f}ms | p99={overall['p99']:.0f}ms\n"
        f"• rules: p95<={OVERALL_P95_MS}ms, p99<={OVERALL_P99_MS}ms, errors<={OVERALL_ERR_PCT:.2f}%\n"
    )
    if overall_breaches:
        msg += "• breaches: " + ", ".join(overall_breaches) + "\n"

    msg += txn_text

    if top_labels:
        msg += "\n*Top slow labels (by p95):*\n"
        for lp95, lavg, le, t, label in top_labels:
            msg += f"• `{label}` — p95 {lp95:.0f}ms, avg {lavg:.0f}ms, err {le:.2f}% (n={t})\n"

    # Slack always posts
    slack_post(token, channel, msg)
    print("SLA+Summary message posted to Slack.")

    # ---- GitHub annotations (optional) ----
    if args.annotations:
        # Always show a compact summary annotation
        base_summary = (
            f"{test_name} | samples={overall['total']} err={overall['err_pct']:.2f}% "
            f"p95={overall['p95']:.0f}ms p99={overall['p99']:.0f}ms"
        )
        if duration_s and rps is not None:
            base_summary += f" | duration={duration_s:.0f}s rps={rps:.2f}"

        if any_breach:
            # INFO mode => warning. Gate mode => error.
            if args.fail_on_breach:
                gha_error("SLA BREACH: " + base_summary)
            else:
                gha_warning("SLA BREACH (INFO): " + base_summary)

            if overall_breaches:
                (gha_error if args.fail_on_breach else gha_warning)("Overall breaches: " + "; ".join(overall_breaches))
            if txn_total > 0 and txn_breaches:
                (gha_error if args.fail_on_breach else gha_warning)(f"Txn breaches ({TXN_LABEL}): " + "; ".join(txn_breaches))
        else:
            gha_notice("SLA OK: " + base_summary)

    # Exit code control
    if any_breach and args.fail_on_breach:
        return 2
    return 0

if __name__ == "__main__":
    try:
        rc = main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}")
        rc = 2
    raise SystemExit(rc)
