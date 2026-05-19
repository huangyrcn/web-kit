#!/usr/bin/env python3
"""Search speed test for web-kit-backend.

Measures:
  - Per-engine latency (direct hit on /google, /ddg, /bing route — bypasses SearxNG aggregation)
  - End-to-end SearxNG aggregated search latency
  - Concurrent throughput at the configured Semaphore(3) level
  - P50/P95 distributions

Run from the host (uses localhost:8082 / docker-exec'd :3100). No external deps:
the script uses only stdlib (urllib + concurrent.futures).
"""

import argparse
import concurrent.futures as cf
import json
import os
import statistics
import time
import urllib.parse
import urllib.request


SEARXNG = None  # set in main() from --target / SEARXNG_URL

QUERIES = [
    "python typing extensions",
    "openwrt clash docker",
    "kubernetes ingress nginx",
    "rust async runtime",
    "postgres jsonb index",
    "react server components",
    "docker compose healthcheck",
    "nginx reverse proxy",
    "go http2 client",
    "wireguard config",
]


def time_one(url, timeout=30):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read())
            elapsed = time.perf_counter() - t0
            return elapsed, r.status, data
    except Exception as e:
        return time.perf_counter() - t0, 0, {"error": str(e)}


def pct(xs, p):
    if not xs:
        return 0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def report(name, samples):
    if not samples:
        print(f"  {name}: no samples")
        return
    times = [s[0] for s in samples]
    oks = [s for s in samples if s[1] == 200 and s[2] > 0]
    avg_r = statistics.mean([s[2] for s in oks]) if oks else 0
    print(f"  {name:<32} n={len(samples):>2} ok={len(oks):>2}  "
          f"p50={pct(times,50)*1000:>6.0f}ms  p95={pct(times,95)*1000:>6.0f}ms  "
          f"min={min(times)*1000:>6.0f}  max={max(times)*1000:>6.0f}  "
          f"avg_results={avg_r:.1f}")


def test_searxng(queries, n):
    samples = []
    for q in queries[:n]:
        url = f"{SEARXNG}/search?q={urllib.parse.quote_plus(q)}&format=json"
        elapsed, status, data = time_one(url)
        results = len(data.get("results", [])) if isinstance(data, dict) else 0
        samples.append((elapsed, status, results))
    return samples


def test_concurrent(queries, concurrency):
    def one(q):
        url = f"{SEARXNG}/search?q={urllib.parse.quote_plus(q)}&format=json"
        return time_one(url)

    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
        results = list(ex.map(one, queries))
    wall = time.perf_counter() - t0
    samples = [(e, s, len(d.get("results", [])) if isinstance(d, dict) else 0)
               for e, s, d in results]
    return samples, wall


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--num", type=int, default=5,
                        help=f"queries per test (default 5, max {len(QUERIES)})")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="parallel test concurrency (default 3 = matches server semaphore)")
    parser.add_argument("--target", type=str,
                        default=os.environ.get("SEARXNG_URL", "http://localhost:8082"),
                        help="SearxNG base URL (default $SEARXNG_URL or http://localhost:8082)")
    parser.add_argument("--skip-cold", action="store_true",
                        help="skip the 30s idle cold-start sample at the end")
    args = parser.parse_args()

    global SEARXNG
    SEARXNG = args.target.rstrip("/")

    n = min(args.num, len(QUERIES))
    print(f"\n=== web-kit-backend search speed test ===")
    print(f"target: {SEARXNG}   n={n} per case\n")

    print("[1/3] SearxNG aggregated (all engines, sequential):")
    samples = test_searxng(QUERIES, n)
    report("sequential", samples)
    print()

    print(f"[2/3] SearxNG concurrent x{args.concurrency} (n={n}):")
    cqueries = (QUERIES * ((n // len(QUERIES)) + 1))[:n]
    samples, wall = test_concurrent(cqueries, args.concurrency)
    report(f"concurrent (wall={wall:.1f}s)", samples)
    print()

    if not args.skip_cold:
        print("[3/3] cold-start sample (after 30s idle):")
        time.sleep(30)
        samples = test_searxng(QUERIES[:1], 1)
        report("after 30s idle", samples)
        print()


if __name__ == "__main__":
    main()
