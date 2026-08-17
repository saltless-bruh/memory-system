#!/usr/bin/env python3
"""Verification script for Scenario 8 (Protected Branch Lockdown) and Scenario 9 (Concurrent Webhook Sync Stress)."""

import asyncio
import hashlib
import hmac
import json
import subprocess
import time
import httpx


def test_scenario_8_protected_branch():
    print("\n" + "=" * 60)
    print("SCENARIO 8: Protected Branch Lockdown Verification")
    print("=" * 60)

    # 1. Check current branch
    branch_proc = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    )
    branch = branch_proc.stdout.strip()
    print(f"Current git branch: {branch!r}")

    # 2. Run healer.py --ci
    print("Executing `uv run python scout/healer.py --ci`...")
    t0 = time.perf_counter()
    proc = subprocess.run(
        ["uv", "run", "python", "scout/healer.py", "--ci"],
        capture_output=True,
        text=True,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"Exit code: {proc.returncode}")
    print(f"Stdout:\n{proc.stdout.strip()}")
    if proc.stderr:
        print(f"Stderr:\n{proc.stderr.strip()}")
    print(f"Execution time: {elapsed_ms:.2f} ms")

    # Assertions
    assert proc.returncode == 1, f"Expected returncode 1, got {proc.returncode}"
    assert "Refusing CI heal on protected branch 'main'" in proc.stdout, (
        f"Expected refusal message not found in stdout: {proc.stdout}"
    )
    print(">>> Scenario 8: PASSED (Lockdown successfully blocked CI healing on protected branch).")
    return {
        "status": "PASS",
        "branch": branch,
        "exit_code": proc.returncode,
        "stdout": proc.stdout.strip(),
        "elapsed_ms": elapsed_ms,
    }


async def test_scenario_9_concurrent_webhooks(concurrency: int = 5):
    print("\n" + "=" * 60)
    print(f"SCENARIO 9: Concurrent Webhook Ingress Stress Test ({concurrency} concurrent requests)")
    print("=" * 60)

    url = "http://localhost:9000/hooks/wiki-update"
    secret = b"dev-secret"

    async def send_single_webhook(client: httpx.AsyncClient, req_id: int):
        payload_dict = {
            "ref": "refs/heads/main",
            "before": f"000000000000000000000000000000000000000{req_id}",
            "after": f"111111111111111111111111111111111111111{req_id}",
            "commits": [
                {
                    "id": f"111111111111111111111111111111111111111{req_id}",
                    "message": f"Stress test commit #{req_id}",
                    "timestamp": "2026-08-17T14:52:00Z",
                }
            ],
            "repository": {
                "name": "wiki",
                "clone_url": "http://gitea:3000/snp/wiki.git",
            },
            "pusher": {"username": "subagent-b3"},
        }
        body_bytes = json.dumps(payload_dict).encode("utf-8")
        sig = hmac.new(secret, body_bytes, hashlib.sha256).hexdigest()

        headers = {
            "X-Gitea-Signature": sig,
            "X-Gitea-Event": "push",
            "Content-Type": "application/json",
        }

        t_start = time.perf_counter()
        resp = await client.post(url, content=body_bytes, headers=headers, timeout=10.0)
        t_end = time.perf_counter()
        lat_ms = (t_end - t_start) * 1000

        return {
            "req_id": req_id,
            "status_code": resp.status_code,
            "json": resp.json() if resp.status_code == 200 else resp.text,
            "latency_ms": lat_ms,
        }

    async with httpx.AsyncClient() as client:
        # Dispatch 5 concurrent requests
        t_all_start = time.perf_counter()
        tasks = [send_single_webhook(client, i + 1) for i in range(concurrency)]
        results = await asyncio.gather(*tasks)
        total_time_ms = (time.perf_counter() - t_all_start) * 1000

    print(f"\nDispatched {concurrency} requests concurrently in {total_time_ms:.2f} ms total:")
    for res in results:
        print(
            f"  Request #{res['req_id']}: HTTP {res['status_code']} | "
            f"Latency: {res['latency_ms']:.2f} ms | Response: {res['json']}"
        )
        assert res["status_code"] == 200, f"Request #{res['req_id']} failed with code {res['status_code']}"
        assert res["json"].get("status") == "success", f"Request #{res['req_id']} returned {res['json']}"

    latencies = [r["latency_ms"] for r in results]
    min_lat = min(latencies)
    max_lat = max(latencies)
    avg_lat = sum(latencies) / len(latencies)

    print(f"\nLatency Statistics:")
    print(f"  Min:  {min_lat:.2f} ms")
    print(f"  Avg:  {avg_lat:.2f} ms")
    print(f"  Max:  {max_lat:.2f} ms")
    print(f"  Total Batch Time: {total_time_ms:.2f} ms")
    print(">>> Scenario 9: PASSED (All concurrent webhook requests returned HTTP 200 with status 'success').")

    # Give background tasks 2 seconds to execute git sync
    print("\nAwaiting background task queue completion (2s)...")
    await asyncio.sleep(2.0)

    # 4. Check docker compose logs
    print("\nDocker compose logs for `host-sync`:")
    logs_proc = subprocess.run(
        ["docker", "compose", "logs", "--tail=25", "host-sync"],
        capture_output=True,
        text=True,
    )
    print(logs_proc.stdout.strip())

    return {
        "status": "PASS",
        "concurrency": concurrency,
        "results": results,
        "min_latency_ms": min_lat,
        "avg_latency_ms": avg_lat,
        "max_latency_ms": max_lat,
        "total_batch_time_ms": total_time_ms,
        "logs": logs_proc.stdout.strip(),
    }


def main():
    s8_res = test_scenario_8_protected_branch()
    s9_res = asyncio.run(test_scenario_9_concurrent_webhooks(5))
    print("\n" + "=" * 60)
    print("ALL TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
