"""
GDB Python script: pass SIGSEGV to the JVM only when it originates from
a JVM safepoint polling page; stop for all other segfaults.

Load via a GDB setupCommand:
  "source /path/to/jvm_sigsegv.py"

How JVM safepoint polling works
--------------------------------
The JVM arms a safepoint by mprotect-ing a per-process "polling page" to
PROT_NONE.  Every compiled Java thread reads from that page in its poll
loop; the resulting SIGSEGV is caught by the JVM's own signal handler which
then drives the thread to the nearest safepoint.  When GDB intercepts the
signal first, we must decide: is this fault from the polling page (pass it
on) or a real crash (stop)?

Detection strategy (in order of reliability)
---------------------------------------------
1. Symbol lookup: read SafepointMechanism::_polling_page directly.
   Works when libjvm has debug symbols or at least the export table.
2. /proc/<pid>/maps scan: find anonymous read-only pages whose size is
   exactly one system page and whose address matches the fault address.
   Works without symbols but is O(map entries).
3. Fallback: stop (conservative — better a false positive than a missed
   crash).
"""

import gdb
import os
import sys


def _page_size() -> int:
    try:
        return os.sysconf("SC_PAGESIZE")
    except (AttributeError, ValueError):
        return 4096


PAGE_SIZE = _page_size()


def _polling_page_from_symbol() -> int | None:
    """Return the safepoint polling page address via JVM debug symbol."""
    for sym in (
        "SafepointMechanism::_polling_page",
        "_ZN18SafepointMechanism13_polling_pageE",  # mangled
    ):
        try:
            val = gdb.parse_and_eval(sym)
            addr = int(val.cast(gdb.lookup_type("unsigned long")))
            if addr:
                return addr & ~(PAGE_SIZE - 1)  # page-align
        except gdb.error:
            pass
    return None


def _is_safepoint_page_from_maps(fault_addr: int) -> bool:
    """
    Check /proc/<pid>/maps: the fault address is on an anonymous, non-writable,
    single-page mapping — consistent with a safepoint polling page.
    This is a heuristic; it catches the common case without debug symbols.
    """
    try:
        inferior = gdb.selected_inferior()
        pid = inferior.pid
        if not pid:
            return False
        page_start = fault_addr & ~(PAGE_SIZE - 1)
        with open(f"/proc/{pid}/maps") as f:
            for line in f:
                # Format: start-end perms offset dev inode [path]
                parts = line.split()
                if len(parts) < 5:
                    continue
                addrs, perms = parts[0], parts[1]
                path = parts[5] if len(parts) >= 6 else ""
                # Must be anonymous (no file path, not heap/stack labels)
                if path and path not in ("", "[anon]"):
                    continue
                start_s, end_s = addrs.split("-")
                start, end = int(start_s, 16), int(end_s, 16)
                if start != page_start:
                    continue
                # Exactly one page, not writable
                if (end - start) != PAGE_SIZE:
                    continue
                if "w" not in perms:
                    return True
    except Exception:
        pass
    return False


class JvmSigsegvHandler:
    """
    Registered on gdb.events.stop; intercepts SIGSEGV and decides whether
    to pass it to the inferior (safepoint) or stop for the user (real crash).
    """

    def __init__(self):
        gdb.events.stop.connect(self._on_stop)
        # Tell GDB not to stop on SIGSEGV at all — we handle it ourselves.
        gdb.execute("handle SIGSEGV nostop noprint pass", to_string=True)
        print("[jvm_sigsegv] Selective SIGSEGV filter active.")

    def _fault_address(self) -> int | None:
        """Read si_addr from $_siginfo."""
        try:
            val = gdb.parse_and_eval("$_siginfo._sifields._sigfault.si_addr")
            return int(val.cast(gdb.lookup_type("unsigned long")))
        except gdb.error:
            return None

    def _on_stop(self, event: gdb.StopEvent) -> None:
        if not isinstance(event, gdb.SignalEvent):
            return
        if event.stop_signal != "SIGSEGV":
            return

        fault_addr = self._fault_address()
        if fault_addr is None:
            # Can't read si_addr — be conservative and stop.
            gdb.execute("handle SIGSEGV stop print nopass", to_string=True)
            print("[jvm_sigsegv] SIGSEGV (si_addr unreadable) — stopping.")
            return

        # --- Check 1: symbol lookup ---
        polling_page = _polling_page_from_symbol()
        if polling_page is not None:
            page_start = polling_page & ~(PAGE_SIZE - 1)
            if page_start <= fault_addr < page_start + PAGE_SIZE:
                gdb.execute("continue", to_string=True)
                return
            # Symbol found but address doesn't match — real crash.
            _stop_with_message(fault_addr, "not a safepoint page (symbol check)")
            return

        # --- Check 2: /proc/<pid>/maps heuristic ---
        if _is_safepoint_page_from_maps(fault_addr):
            gdb.execute("continue", to_string=True)
            return

        _stop_with_message(fault_addr, "no safepoint page found")


def _stop_with_message(fault_addr: int, reason: str) -> None:
    print(
        f"[jvm_sigsegv] SIGSEGV at 0x{fault_addr:x} ({reason}) — stopping."
    )
    # Re-enable stop so the user sees this signal normally.
    gdb.execute("handle SIGSEGV stop print nopass", to_string=True)


# Install the handler when this script is sourced.
_handler = JvmSigsegvHandler()
