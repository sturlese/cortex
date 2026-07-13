"""clean entrypoint: inventory -> pending -> process (concurrently) -> state.

Turns the raw file mirror into clean Markdown knowledge-base pages. Each document is handled by
an agentic worker with bounded autonomy (tools: read_more, ocr) judged by a deterministic
verifier. `--once` for a single pass; otherwise loops every CLEAN_INTERVAL_SECONDS.
"""
import argparse
import asyncio
import datetime
import os
import re
import time

from clean.agents import build_agent
from clean.entity import build_catalog
from clean.playbook import load_playbook
from clean.settings import Settings
from clean.state import classify_pending, load_inventory, load_state, save_state
from clean.worker import process_one

SAVE_INTERVAL_SECONDS = 5.0
# Progress saves are throttled: writing the whole state after EVERY document is O(n²) bytes over
# a pass (10k docs ≈ rewriting a multi-MB file 10k times). A crash now replays at most the last
# few seconds of documents — harmless, since processing is idempotent by content hash.


def log(msg):
    print(f"[clean {datetime.datetime.now(datetime.UTC).isoformat()}] {msg}", flush=True)


def is_rate_limit(ex) -> bool:
    """A 429 lands here only after the API client exhausted its own retries. Match the status code
    or a standalone 429 token — NOT any '429' substring, which would trip on byte offsets/sizes in
    unrelated error messages and wedge the whole pass on a poisoned document."""
    return getattr(ex, "status_code", None) == 429 or bool(re.search(r"\b429\b", str(ex)))


def dedup_pending(pending, state, inventory, brain_md_dir=None):
    """Exact content dedup: a pending doc whose sha256 already has a processed page (or an earlier
    pending doc in this pass) becomes `duplicate` -> no LLM call, no page, state points at the
    canonical file id. Deterministic: existing processed entries win; within a pass, lowest id."""
    canonical = {}
    for fid, f in state.get("files", {}).items():
        if f.get("status") == "processed" and f.get("rawHash") and fid in inventory:
            canonical.setdefault(f["rawHash"], fid)
    kept, duplicates = [], 0
    now = datetime.datetime.now(datetime.UTC).isoformat()
    for doc in sorted(pending, key=lambda d: d["fileId"]):
        h = doc.get("rawHash")
        if doc["reason"] == "deleted" or not h:
            kept.append(doc)
            continue
        canon = canonical.setdefault(h, doc["fileId"])
        if canon == doc["fileId"]:
            kept.append(doc)
        else:
            duplicates += 1
            e = doc["entry"]
            # a previously-processed doc that turned into a duplicate leaves a stale page behind:
            # remove it so brain-md doesn't keep outdated content the state no longer points at.
            prev_path = (state["files"].get(doc["fileId"], {}).get("lastResult") or {}).get("path")
            if brain_md_dir and prev_path:
                try:
                    os.remove(os.path.join(brain_md_dir, prev_path))
                    log(f"DELETED page {prev_path} ({doc['fileId']} now a duplicate of {canon})")
                except FileNotFoundError:
                    pass
            state["files"][doc["fileId"]] = {
                "name": e.get("name"), "localPath": e.get("localPath"), "rawHash": h,
                "status": "duplicate", "duplicateOf": canon, "updatedAt": now,
            }
            log(f"DUPLICATE {doc['fileId']} == {canon} (same content) -> no page")
    return kept, duplicates


async def run_once(cfg: Settings) -> dict:
    for d in (cfg.state_dir, cfg.brain_md_dir):
        os.makedirs(d, exist_ok=True)
    inventory = load_inventory(cfg.raw_dir)
    if not inventory:
        log(f"no inventory ({cfg.raw_dir}/_state.json)")
        return {"total": 0, "pending": 0, "processed": 0, "errors": 0}

    state = load_state(cfg.state_dir)
    pending = classify_pending(inventory, state, cfg.raw_dir)
    pending, duplicates = dedup_pending(pending, state, inventory, cfg.brain_md_dir)
    if cfg.max_docs > 0 and len(pending) > cfg.max_docs:
        log(f"limiting to {cfg.max_docs} docs (CLEAN_MAX_DOCS) out of {len(pending)} pending")
        pending = pending[: cfg.max_docs]

    if duplicates:
        save_state(cfg.state_dir, state)
    if not pending:
        return {"total": len(inventory), "pending": 0, "processed": 0, "errors": 0, "duplicates": duplicates}

    processor = build_agent(playbook=load_playbook(cfg.state_dir))
    # entity catalog (high confidence, over the WHOLE inventory) for the second, path-based pass
    catalog = build_catalog([(e.get("drivePath", ""), e.get("orgUnit")) for e in inventory.values()])
    sem = asyncio.Semaphore(cfg.max_concurrent)
    stats = {"processed": 0, "errors": 0, "skipped": 0, "in_tok": 0, "out_tok": 0, "reasoning_tok": 0}
    abort = {"rate_limit": False, "budget": False}  # circuit breakers: provider limit / token budget
    total = len(pending)
    start = datetime.datetime.now(datetime.UTC)
    last_save = [0.0]

    def save_progress():
        now = time.monotonic()
        if now - last_save[0] >= SAVE_INTERVAL_SECONDS:
            last_save[0] = now
            save_state(cfg.state_dir, state)

    def maybe_report():
        n = stats["processed"] + stats["errors"]
        if n == 0 or n % 100 != 0:
            return
        el_min = (datetime.datetime.now(datetime.UTC) - start).total_seconds() / 60.0
        rate = n / el_min if el_min > 0 else 0
        eta_h = ((total - n) / rate / 60.0) if rate > 0 else 0
        ok = stats["processed"] - stats["skipped"]
        log(f"progress {n}/{total} · ok={ok} skip={stats['skipped']} err={stats['errors']} "
            f"· {rate:.1f} docs/min · elapsed {el_min/60:.1f}h · ETA ~{eta_h:.1f}h")

    async def worker(doc):
        file_id = doc["fileId"]
        now = datetime.datetime.now(datetime.UTC).isoformat()
        async with sem:
            if abort["rate_limit"] or abort["budget"]:
                return  # pass aborted: leave the doc untouched -> still pending, retried on relaunch
            if doc["reason"] == "deleted":
                # source gone from Drive -> remove its page too (deletions propagate end to end)
                prev = state["files"].get(file_id, {})
                rel = (prev.get("lastResult") or {}).get("path")
                if rel:
                    try:
                        os.remove(os.path.join(cfg.brain_md_dir, rel))
                        log(f"DELETED page {rel} (source removed)")
                    except FileNotFoundError:
                        pass
                state["files"][file_id] = {**prev, "status": "deleted", "updatedAt": now}
                stats["processed"] += 1
                maybe_report()
                return
            try:
                res = await process_one(doc, processor, cfg.raw_dir, cfg.brain_md_dir, catalog)
                e = doc["entry"]
                # a rename/move changes the page's slug or entity folder: delete the previous page
                # so the stale copy (with outdated content) doesn't linger in brain-md forever.
                old_path = (state["files"].get(file_id, {}).get("lastResult") or {}).get("path")
                new_path = res.get("path")
                if old_path and old_path != new_path:
                    try:
                        os.remove(os.path.join(cfg.brain_md_dir, old_path))
                        log(f"DELETED stale page {old_path} (renamed/moved -> {new_path})")
                    except FileNotFoundError:
                        pass
                state["files"][file_id] = {
                    "name": e.get("name"), "mimeType": e.get("mimeType"), "localPath": e.get("localPath"),
                    "sourceUri": e.get("sourceUri"), "rawHash": doc["rawHash"],
                    "status": "processed", "lastResult": res, "updatedAt": now,
                }
                stats["processed"] += 1
                if res.get("skipped"):
                    stats["skipped"] += 1
                verdict = res.get("verification")
                if verdict:
                    stats[f"verify_{verdict}"] = stats.get(f"verify_{verdict}", 0) + 1
                    if verdict == "failed":
                        log(f"VERIFY FAILED {res.get('path')}: unverified figures {res.get('unverified_numbers')}")
                if res.get("unanchored_numbers"):
                    stats["verify_unanchored"] = stats.get("verify_unanchored", 0) + 1
                    log(f"VERIFY UNANCHORED {res.get('path')}: figures tied to a period the source "
                        f"contradicts {res.get('unanchored_numbers')}")
                if res.get("ocr"):
                    stats["ocr_docs"] = stats.get("ocr_docs", 0) + 1
                if res.get("retried"):
                    stats["verify_retries"] = stats.get("verify_retries", 0) + 1
                tk = res.get("usage") or {}
                stats["in_tok"] += tk.get("in", 0)
                stats["out_tok"] += tk.get("out", 0)
                stats["reasoning_tok"] += tk.get("reasoning", 0)
                if cfg.token_budget and stats["in_tok"] + stats["out_tok"] >= cfg.token_budget:
                    # hard spend ceiling: finish this doc, stop taking new ones. Docs stay pending.
                    abort["budget"] = True
                    log(f"TOKEN BUDGET reached ({stats['in_tok'] + stats['out_tok']}/{cfg.token_budget}) "
                        "-> pausing pass; remaining docs stay pending")
                tinfo = f" · {tk.get('in', 0)}+{tk.get('out', 0)}tok" if tk else ""
                rep = res.get('representation') or '-'
                vinfo = f" · {verdict}" if verdict and verdict != "verified" else ""
                if res.get("retried"):
                    vinfo += " · self-corrected"
                log(f"OK {res.get('path') or 'skipped'} ({res.get('method')}/{rep}){vinfo}{tinfo}")
            except Exception as ex:  # noqa: BLE001
                if is_rate_limit(ex):
                    # hard/long plan limit: abort the pass cleanly instead of burning the backlog.
                    # The doc is NOT marked as error -> stays pending, resumes on relaunch.
                    abort["rate_limit"] = True
                    log(f"persistent RATE LIMIT on {file_id} (retries exhausted) -> "
                        "aborting pass; relaunch later to resume")
                    return
                stats["errors"] += 1
                state["files"][file_id] = {**state["files"].get(file_id, {}), "status": "error",
                                           "error": str(ex)[:600], "updatedAt": now}
                log(f"ERROR {file_id}: {str(ex)[:200]}")
            maybe_report()
        save_progress()

    await asyncio.gather(*(worker(d) for d in pending))
    save_state(cfg.state_dir, state)   # final save is unconditional — nothing may be lost at rest
    return {"total": len(inventory), "pending": len(pending), "duplicates": duplicates,
            "aborted": abort["rate_limit"], "aborted_budget": abort["budget"], **stats}


async def main(cfg: Settings, once: bool = False):
    from clean.observability import maybe_instrument
    maybe_instrument("clean")
    log(f"start raw={cfg.raw_dir} out={cfg.brain_md_dir} dryRun={cfg.dry_run} "
        f"maxConcurrent={cfg.max_concurrent}")
    while True:
        if cfg.dry_run:
            # No-op pass, but KEEP LOOPING: the default compose service has restart:unless-stopped,
            # so returning here would make the container exit-restart forever instead of idling.
            log("CLEAN_DRY_RUN=true -> no-op pass. Set it to false to process.")
        else:
            stats = await run_once(cfg)
            it, ot, rt = stats.get("in_tok", 0), stats.get("out_tok", 0), stats.get("reasoning_tok", 0)
            if it or ot:
                log(f"tokens: in={it} out={ot} reasoning={rt}")
            if stats.get("aborted"):
                log(f"pass ABORTED by rate limit {stats} — relaunch later to resume "
                    "(pending docs retry on their own)")
            else:
                log(f"pass OK {stats}")
        if once:
            break
        await asyncio.sleep(cfg.interval)


def cli(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="clean", description="Agentic ingestion worker: raw file mirror -> verified brain-md pages.")
    parser.add_argument("--once", action="store_true", help="run a single pass instead of looping")
    args = parser.parse_args(argv)
    asyncio.run(main(Settings.from_env(), once=args.once))


if __name__ == "__main__":
    cli()
