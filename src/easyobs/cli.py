import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from easyobs.settings import get_settings


def _pids_listening(port: int) -> set[int]:
    """Best-effort: return PIDs holding a LISTEN socket on ``port``.

    Cross-platform via ``psutil`` if available; otherwise falls back to
    ``netstat -ano`` (Windows) / ``lsof -ti`` (POSIX). Always returns a
    set — empty if nothing was found or the helper failed.
    """
    pids: set[int] = set()
    try:  # psutil is the cleanest path but optional.
        import psutil  # type: ignore

        for c in psutil.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                pids.add(int(c.pid))
        return pids
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            raw = subprocess.check_output(
                ["netstat", "-ano"],
                text=True,
                stderr=subprocess.DEVNULL,
                errors="ignore",
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return pids
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0] != "TCP" or parts[3] != "LISTENING":
                continue
            local = parts[1]
            if not local.endswith(f":{port}"):
                continue
            try:
                pids.add(int(parts[4]))
            except ValueError:
                pass
    else:
        try:
            raw = subprocess.check_output(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], text=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return pids
        for line in raw.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.add(int(line))
    return pids


def _list_python_processes() -> list[tuple[int, int, str]]:
    """Snapshot every live python process as ``(pid, ppid, cmdline_lower)``.

    Tries ``psutil`` first (handles commas-in-CommandLine cleanly),
    then PowerShell ``Get-CimInstance`` on Windows, then ``ps`` on POSIX.
    """
    out: list[tuple[int, int, str]] = []
    try:
        import psutil  # type: ignore

        for p in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
            try:
                name = (p.info.get("name") or "").lower()
                if not name.startswith("python"):
                    continue
                cmdl = " ".join(p.info.get("cmdline") or [])
                out.append((int(p.info["pid"]), int(p.info["ppid"]), cmdl.lower()))
            except Exception:
                continue
        if out:
            return out
    except Exception:
        pass

    if sys.platform.startswith("win"):
        # `wmic` is deprecated on Windows 11 and its CSV output mangles
        # CommandLines that contain commas (e.g. `spawn_main(parent_pid=…,
        # pipe_handle=…)`). PowerShell Get-CimInstance with a custom
        # delimiter survives both issues.
        ps_script = (
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
            "-ErrorAction SilentlyContinue | ForEach-Object { "
            "'{0}|{1}|{2}' -f $_.ProcessId, $_.ParentProcessId, "
            "(($_.CommandLine -replace '\\r?\\n',' ') -as [string]) }"
        )
        try:
            raw = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    ps_script,
                ],
                text=True,
                stderr=subprocess.DEVNULL,
                errors="ignore",
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return out
        for line in raw.splitlines():
            parts = line.split("|", 2)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0].strip())
                ppid = int(parts[1].strip())
            except ValueError:
                continue
            cmdl = (parts[2].strip().lower() if len(parts) > 2 else "")
            out.append((pid, ppid, cmdl))
    else:
        try:
            raw = subprocess.check_output(
                ["ps", "-eo", "pid,ppid,args"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return out
        for line in raw.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            try:
                pid_s, ppid_s, *rest = line.split(maxsplit=2)
                pid = int(pid_s)
                ppid = int(ppid_s)
            except ValueError:
                continue
            cmdl = (rest[0] if rest else "").lower()
            if "python" not in cmdl:
                continue
            out.append((pid, ppid, cmdl))
    return out


def _descendants(root: int, by_parent: dict[int, list[int]]) -> set[int]:
    """Breadth-first descendant collection from a parent->children map."""
    out: set[int] = set()
    queue: list[int] = [root]
    while queue:
        cur = queue.pop()
        for child in by_parent.get(cur, ()):
            if child not in out and child != root:
                out.add(child)
                queue.append(child)
    return out


def _kill_pids(pids: set[int]) -> None:
    if not pids:
        return
    if sys.platform.startswith("win"):
        for tid in sorted(pids):
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(tid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                pass
        # Belt-and-braces: psutil terminate handles cases where the PID
        # taskkill matched is already dead but a sibling slipped through.
        try:
            import psutil  # type: ignore

            for tid in sorted(pids):
                try:
                    psutil.Process(tid).kill()
                except Exception:
                    continue
        except Exception:
            pass
    else:
        import os
        import signal

        for tid in sorted(pids):
            try:
                os.kill(tid, signal.SIGKILL)
            except ProcessLookupError:
                continue


def _stop_easyobs_servers(api_port: int | None = None) -> int:
    """Aggressively reap every easyobs API process and its children.

    The hard case is Windows uvicorn ``--reload``: a reloader parent spawns
    a worker via ``multiprocessing``. The child inherits the LISTEN socket
    on ``api_port`` *and* the open ``catalog.sqlite3`` handle. Killing the
    parent does **not** always reap that child, and the OS keeps reporting
    the port as owned by the (now dead) parent PID, so a naive
    ``taskkill /T /F /PID <port_owner>`` is a no-op while the orphan still
    holds the file. This loop reaps such orphans by walking the live
    process table and treating any python process whose parent is gone (or
    is one of our targets) as a kill target, and by reaping the children
    of any dead PID still attributed to the LISTEN socket.

    Returns the cumulative number of distinct PIDs we asked the OS to kill.
    """
    killed: set[int] = set()

    for _attempt in range(3):
        rows = _list_python_processes()
        live_pids = {pid for pid, _, _ in rows}
        by_parent: dict[int, list[int]] = {}
        for pid, ppid, _ in rows:
            by_parent.setdefault(ppid, []).append(pid)

        targets: set[int] = set()

        # 1) Direct uvicorn easyobs parents and everything underneath them.
        for pid, _ppid, cmdl in rows:
            if "uvicorn" in cmdl and "easyobs" in cmdl:
                targets.add(pid)
                targets |= _descendants(pid, by_parent)

        # 2) Port holders + their orphaned children when the holder PID is
        #    already dead (Windows attributes the LISTEN to the dead
        #    parent; the actual handle holder is the surviving child).
        if api_port is not None:
            holders = _pids_listening(api_port)
            for p in holders:
                if p == 0:
                    continue
                if p in live_pids:
                    targets.add(p)
                    targets |= _descendants(p, by_parent)
                else:
                    children = by_parent.get(p, [])
                    if children:
                        print(
                            f"[easyobs] reaping orphans of dead pid={p}: "
                            + ", ".join(str(c) for c in children)
                        )
                    for child in children:
                        targets.add(child)
                        targets |= _descendants(child, by_parent)

        # 3) Any multiprocessing worker whose parent is gone or already a
        #    kill target — these are exactly the orphans that survive a
        #    parent-only taskkill and keep the catalog file locked.
        for pid, ppid, cmdl in rows:
            if "multiprocessing" not in cmdl:
                continue
            if ppid in live_pids and ppid not in targets:
                continue
            targets.add(pid)
            targets |= _descendants(pid, by_parent)

        targets -= killed
        targets.discard(0)
        if not targets:
            break

        if api_port is not None and not killed:
            holders = _pids_listening(api_port)
            if holders:
                print(
                    f"[easyobs] processes holding port {api_port}: "
                    + ", ".join(str(p) for p in sorted(holders))
                )

        _kill_pids(targets)
        for t in sorted(targets):
            print(f"[easyobs] terminated pid={t}")
        killed |= targets
        # Brief pause so the OS releases file/socket handles before the
        # next listing -- otherwise we re-discover the same PIDs.
        time.sleep(0.8)

    return len(killed)


def _run_migrate_parquet(args) -> None:
    """Convert NDJSON blobs to Parquet format."""
    import json

    try:
        import pyarrow.parquet as pq
    except ImportError:
        print(
            "[easyobs] pyarrow is required for migration. "
            "Install with: pip install easyobs[analytics]",
            file=sys.stderr,
        )
        sys.exit(1)

    from easyobs.ingest.parquet_schema import span_dicts_to_arrow_table

    s = get_settings()
    if args.data_dir:
        blob_root = Path(args.data_dir) / "blob"
    else:
        blob_root = s.blob_root

    if not blob_root.exists():
        print(f"[easyobs] blob root does not exist: {blob_root}")
        return

    jsonl_files = list(blob_root.rglob("*.jsonl"))
    if not jsonl_files:
        print(f"[easyobs] no .jsonl files found under {blob_root}")
        return

    print(f"[easyobs] found {len(jsonl_files)} NDJSON files to convert")
    converted = 0
    failed = 0

    for jsonl_path in jsonl_files:
        try:
            lines: list[dict] = []
            with jsonl_path.open(encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if raw:
                        lines.append(json.loads(raw))

            if not lines:
                if args.delete_source:
                    jsonl_path.unlink()
                continue

            # Derive date partition from span start times or file modification
            first_span = lines[0]
            start_ns = first_span.get("startTimeUnixNano")
            if start_ns and isinstance(start_ns, int):
                from datetime import datetime, timezone as tz
                dt = datetime.fromtimestamp(start_ns / 1e9, tz=tz.utc).strftime("%Y-%m-%d")
            else:
                mtime = jsonl_path.stat().st_mtime
                from datetime import datetime, timezone as tz
                dt = datetime.fromtimestamp(mtime, tz=tz.utc).strftime("%Y-%m-%d")

            table = span_dicts_to_arrow_table(lines, dt=dt)

            parquet_path = jsonl_path.with_suffix(".parquet")
            pq.write_table(table, str(parquet_path), compression="snappy")

            if args.delete_source:
                jsonl_path.unlink()

            converted += 1
            if converted % 100 == 0:
                print(f"[easyobs] progress: {converted}/{len(jsonl_files)} converted")

        except Exception as e:
            failed += 1
            print(f"[easyobs] FAILED {jsonl_path.name}: {e}", file=sys.stderr)

    print(f"[easyobs] migration complete: {converted} converted, {failed} failed")
    if not args.delete_source:
        print("[easyobs] hint: re-run with --delete-source to remove original .jsonl files")


def main() -> None:
    parser = argparse.ArgumentParser(prog="easyobs")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Run API server")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)

    p_reset = sub.add_parser(
        "reset-data",
        help=(
            "Wipe local catalog (catalog.sqlite3), JWT secret and blob "
            "store so the next sign-up bootstraps a fresh super admin."
        ),
    )
    p_reset.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
    p_reset.add_argument(
        "--force",
        action="store_true",
        help=(
            "Also terminate any running easyobs API/ingest server (and its "
            "multiprocessing children) before deleting the files. Use this "
            "when run-dev.ps1 / `easyobs serve` is currently up."
        ),
    )

    p_migrate = sub.add_parser(
        "migrate-parquet",
        help=(
            "Convert existing NDJSON blob data to Parquet format. "
            "Reads all .jsonl files under the blob root, converts them "
            "to Parquet with the standard span schema, and writes the "
            "output to the same directory structure."
        ),
    )
    p_migrate.add_argument(
        "--delete-source",
        action="store_true",
        help="Delete original .jsonl files after successful conversion.",
    )
    p_migrate.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Override the data directory (defaults to EASYOBS_DATA_DIR).",
    )

    args = parser.parse_args()
    if args.cmd == "serve":
        s = get_settings()
        host = args.host or s.api_host
        port = args.port or s.api_port
        uvicorn.run(
            "easyobs.http_app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=False,
        )
    elif args.cmd == "reset-data":
        s = get_settings()
        targets: list[Path] = [
            s.data_dir / "catalog.sqlite3",
            s.data_dir / "jwt.secret",
            s.blob_root,
        ]
        existing = [p for p in targets if p.exists()]
        if not existing:
            print(f"[easyobs] nothing to reset under {s.data_dir}")
            return
        print(f"[easyobs] will remove (under {s.data_dir}):")
        for p in existing:
            kind = "dir" if p.is_dir() else "file"
            print(f"   - {kind:4s} {p}")
        if not args.yes:
            answer = input("Proceed? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("[easyobs] aborted.")
                sys.exit(1)
        if args.force:
            n = _stop_easyobs_servers(api_port=s.api_port)
            if n == 0:
                print("[easyobs] no running easyobs server detected.")

        def _remove(p: Path) -> tuple[bool, str | None]:
            # Windows holds the file handle for a brief moment after the
            # owning process exits, especially for SQLite WAL/SHM siblings.
            # Retry a few times so the user doesn't have to rerun.
            last_err: str | None = None
            for _ in range(8):
                try:
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
                    return True, None
                except OSError as e:
                    last_err = str(e)
                    time.sleep(0.25)
            return False, last_err

        for p in existing:
            ok, err = _remove(p)
            if not ok:
                # If the first attempt failed, try one more aggressive
                # cleanup pass (covers `--force` users whose orphan worker
                # only became visible after the parent died) and retry.
                print(
                    f"[easyobs] {p.name} is locked — attempting to stop "
                    "lingering easyobs workers and retry...",
                    file=sys.stderr,
                )
                _stop_easyobs_servers(api_port=s.api_port)
                ok, err = _remove(p)
            if not ok:
                print(
                    f"[easyobs] FAILED to remove {p}: {err}\n"
                    "  -> stop the dev server manually (Ctrl+C in the "
                    "terminal running run-dev.ps1) and rerun, or use "
                    "`easyobs reset-data --force --yes`.",
                    file=sys.stderr,
                )
                sys.exit(2)
            print(f"[easyobs] removed {p}")

    elif args.cmd == "migrate-parquet":
        _run_migrate_parquet(args)


if __name__ == "__main__":
    main()
