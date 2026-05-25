"""Drive Google MyMaps via Playwright to create a new map from a StudyRequest.

Uses a persistent Chromium profile in %LOCALAPPDATA%\\TrafficIntake\\browser-profile
so first-run users log into Google once, subsequent runs reuse the session.

Flow (per John Goodwin's workflow, 2026-05-14):
  1. Open mymaps "new map" editor
  2. If not signed in, pause for the user to sign in (browser is visible)
  3. Set the map title
  4. For each planned study group (TMC / Tube / Survey × subtype × time-window):
     - First group: import that group's KML into the default 'Untitled layer'
       (MyMaps auto-renames the layer to the KML's <Document><name>)
     - Subsequent groups: click '+ Add layer' to create a new empty layer,
       then import that group's KML — auto-renames the same way
  5. Open Share dialog, set "anyone with the link can view", capture the link

Result: one map layer per qchub study group, with the same descriptive name
on both sides. Mirrors how John structures maps before exporting per-layer
KMLs into qchub. Replaces the old single-layer dump (every location in one
'Untitled layer'-renamed-to-the-KMZ-name layer), which made it impossible
for downstream consumers — qchub, the user, John's process — to tell which
location belonged to which study group.

Brittleness disclaimer: Google's MyMaps UI changes occasionally. We've used
text/aria locators where possible to maximize resilience, and we surface
intermediate progress so failures are easy to diagnose. If a step breaks,
fix the affected `page.get_by_*` line.
"""
from __future__ import annotations

import datetime
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import (
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from . import config, kml_export
from .models import StudyRequest

ProgressCallback = Callable[[str], None]


@dataclass
class CreateMapResult:
    share_url: Optional[str]  # None if we couldn't capture it; the map is still created
    map_edit_url: str  # always set — direct link to the editable MyMaps page
    map_title: str
    note: Optional[str] = None  # human-readable summary of what happened


class MyMapsError(Exception):
    """Generic failure during MyMaps automation."""


class GoogleLoginRequired(MyMapsError):
    """User wasn't signed in and didn't complete sign-in within the timeout."""


# ---------- public entry ----------

def create_mymaps_map(
    request: StudyRequest,
    *,
    progress: Optional[ProgressCallback] = None,
    login_timeout_sec: int = 300,
) -> CreateMapResult:
    """Open MyMaps, create a new map with one layer per study group, share it.

    Per-group KMLs are built internally via kml_export.build_kml_for_locations
    — one per planned study group, mirroring how qchub orders are structured
    (see Ship A). Groups with no geocoded locations are skipped (we don't
    create empty layers).

    `progress` is called with short status strings as the automation moves.
    """
    log = progress or (lambda s: None)
    profile_dir = config.app_data_dir() / "browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    map_title = kml_export.build_map_title(request)

    with sync_playwright() as p:
        return _run(p, request, map_title, profile_dir, log, login_timeout_sec)


def _new_run_dir() -> Path:
    """One directory per automation run, holding per-step snapshots."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = config.app_data_dir() / "mymaps-diagnostics" / f"run-{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_step_snapshot(page: Optional[Page], run_dir: Path, step_index: int, step: str) -> None:
    """Capture page state at the END of a successful step (or any state worth recording).

    Files land in {run_dir}/{NN-step-name}/ so I can audit the whole flow after one run.
    """
    if page is None:
        return
    sub = run_dir / f"{step_index:02d}-{step}"
    sub.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(sub / "screenshot.png"), full_page=True)
    except Exception:
        pass
    try:
        (sub / "page.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        (sub / "url.txt").write_text(getattr(page, "url", "unknown"), encoding="utf-8")
    except Exception:
        pass


def _save_failure_diagnostic(page: Optional[Page], run_dir: Path, step: str, exc: BaseException) -> Optional[Path]:
    """Snapshot the page state when a step fails."""
    if page is None:
        return None
    sub = run_dir / f"FAILURE-{step}"
    sub.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(sub / "screenshot.png"), full_page=True)
    except Exception:
        pass
    try:
        (sub / "page.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        (sub / "details.txt").write_text(
            f"Step: {step}\nURL: {getattr(page, 'url', 'unknown')}\n"
            f"Exception: {type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    return run_dir


# ---------- internals ----------

def _run(
    p: Playwright,
    request: StudyRequest,
    map_title: str,
    profile_dir: Path,
    log: ProgressCallback,
    login_timeout_sec: int,
) -> CreateMapResult:
    run_dir = _new_run_dir()
    # Tee every log() to run.log so post-run timing analysis is possible.
    # Mirrors the qchub diagnostic pattern. Without this, the per-wait
    # timing instrumentation added 2026-05-17 (Import click, picker iframe,
    # inter-group settle breakdown) goes to stdout/chat only and is lost
    # the moment the run ends, making "why was this slow?" questions
    # un-answerable after the fact.
    _run_log_path = run_dir / "run.log"
    try:
        _run_log_fh = _run_log_path.open("a", encoding="utf-8")
    except Exception:
        _run_log_fh = None
    if _run_log_fh is not None:
        _orig_log = log
        def _tee_log(msg: str) -> None:
            try:
                _run_log_fh.write(
                    f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}\n"
                )
                _run_log_fh.flush()
            except Exception:
                pass
            _orig_log(msg)
        log = _tee_log

    log("Launching browser…")
    log(f"(Snapshots for this run → {run_dir})")

    # On ARM64 Windows, Playwright's bundled (x64) Chromium often fails with a
    # 'side-by-side configuration' error because the VC++ runtime it needs isn't
    # installed. Edge ships with Windows 11 and is ARM64-native, so we prefer it.
    context = None
    last_err: Optional[BaseException] = None
    for channel in ("msedge", None):
        try:
            from .runtime_settings import is_headless_mode
            kwargs: dict = dict(
                user_data_dir=str(profile_dir),
                # Headless toggle is a user-settable preference (Settings
                # dialog) — production / mass-deploy installs default ON
                # so map automation runs invisibly; dev / diagnostic runs
                # default OFF so the user can watch.
                headless=is_headless_mode(),
                viewport={"width": 1400, "height": 900},
                accept_downloads=True,
                # Suppress Edge's "controlled by automated test software"
                # banner + its associated download/JS-API restrictions.
                # Same rationale as qchub.py — see comment there.
                ignore_default_args=["--enable-automation"],
                args=["--disable-blink-features=AutomationControlled"],
            )
            if channel:
                kwargs["channel"] = channel
            context = p.chromium.launch_persistent_context(**kwargs)
            log(f"Launched ({'Microsoft Edge' if channel == 'msedge' else 'bundled Chromium'}).")
            break
        except BaseException as exc:
            last_err = exc
            log(f"Browser launch failed ({channel or 'bundled chromium'}): {exc}")
            continue

    if context is None:
        raise MyMapsError(
            "Couldn't launch any browser. Make sure Microsoft Edge is installed "
            f"(it normally ships with Windows 11). Last error: {last_err}"
        )
    page: Optional[Page] = None
    step = "launch"
    step_idx = 0
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(30_000)

        step = "navigate"
        log("Opening MyMaps homepage…")
        page.goto("https://www.google.com/maps/d/", wait_until="domcontentloaded")
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        step = "sign-in"
        page = _ensure_signed_in(context, page, log, login_timeout_sec)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        step = "create-new-map"
        log("Creating a new map…")
        page = _create_new_map(page, log)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        step = "wait-for-editor"
        _wait_for_editor(page, log)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        step = "set-title"
        _set_map_title(page, map_title, log)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        step = "import-per-group-layers"
        _import_per_group_layers(page, request, log, run_dir=run_dir, base_step_idx=step_idx)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        step = "share-dialog-opened"
        # Open share dialog and snapshot BEFORE attempting the toggle/copy moves
        _open_share_dialog(page, log)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        step = "share-link-captured"
        share_url = _capture_share_link(page, log)
        step_idx += 1; _save_step_snapshot(page, run_dir, step_idx, step)

        map_edit_url = page.url
        return CreateMapResult(
            share_url=share_url,
            map_edit_url=map_edit_url,
            map_title=map_title,
            note=(
                f"Map created and KMZ imported. Step snapshots saved to {run_dir}."
                if share_url
                else f"Map created and KMZ imported but share link not captured. "
                     f"Open the map and click Share manually to get the link. "
                     f"Snapshots: {run_dir}."
            ),
        )
    except BaseException as exc:
        diag = _save_failure_diagnostic(page, run_dir, step, exc)
        if isinstance(exc, MyMapsError):
            if diag:
                exc.args = (f"{exc.args[0] if exc.args else str(exc)} (diagnostic: {diag})",)
            raise
        message = f"Step '{step}' failed: {type(exc).__name__}: {exc}"
        if diag:
            message += f" — diagnostics: {diag}"
        raise MyMapsError(message) from exc
    finally:
        # Leave the browser open — the user verifies the result and the session
        # stays warm for next runs.
        if _run_log_fh is not None:
            try:
                _run_log_fh.close()
            except Exception:
                pass


_EDITOR_URL_RE = re.compile(r"google\.com/maps/d/edit\?.*\bmid=", re.IGNORECASE)


def _dismiss_known_consent_dialogs(page: Page, log: ProgressCallback) -> None:
    """Click through Google consent / first-run dialogs that block automation.

    Currently handles:
      - Drive consent on first map creation: 'Creating a MyMaps map always uploads
        title, thumbnail, and associated metadata to Drive' → CREATE button
      - General: cookie / 'I agree' banners

    Each lookup is short (2s) — if no dialog is present we move on quickly.
    """
    consent_patterns = [
        # (text-to-find-on-dialog, button-text-to-click)
        (re.compile(r"uploads.{0,40}(title|thumbnail).{0,40}Drive", re.IGNORECASE), re.compile(r"^\s*create\s*$", re.IGNORECASE)),
        (re.compile(r"before you continue", re.IGNORECASE), re.compile(r"^(accept all|i agree|got it)$", re.IGNORECASE)),
    ]
    for marker, button in consent_patterns:
        try:
            page.get_by_text(marker).first.wait_for(state="visible", timeout=2_000)
        except (PlaywrightTimeoutError, Exception):
            continue
        log(f"Dismissing consent dialog (marker: {marker.pattern!r})…")
        clicked = False
        # Try role=button first (most idiomatic), then any element with exact text
        try:
            page.get_by_role("button", name=button).first.click(timeout=3_000)
            clicked = True
        except Exception:
            pass
        if not clicked:
            try:
                page.get_by_text(button).first.click(timeout=3_000)
                clicked = True
            except Exception:
                pass
        if not clicked:
            log(f"(Saw consent marker {marker.pattern!r} but couldn't click {button.pattern!r})")


def _create_new_map(page: Page, log: ProgressCallback) -> Page:
    """From the MyMaps landing page, click 'Create a new map' and wait for the editor.

    Returns the live editor page (URL has /edit?mid=<id>).
    """
    initial_url = page.url
    log(f"Currently on: {initial_url}")
    log("Looking for 'Create a new map' button…")
    candidates = [
        re.compile(r"^\s*\+?\s*create a new map\s*$", re.IGNORECASE),
        re.compile(r"^\s*create new map\s*$", re.IGNORECASE),
        re.compile(r"^\s*new map\s*$", re.IGNORECASE),
    ]
    clicked = False
    for pattern in candidates:
        try:
            page.get_by_role("button", name=pattern).first.click(timeout=8_000)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        for pattern in candidates:
            try:
                page.get_by_text(pattern).first.click(timeout=5_000)
                clicked = True
                break
            except Exception:
                continue

    if not clicked:
        raise MyMapsError(
            "Couldn't find a 'Create a new map' button on the MyMaps landing page. "
            "The page layout may have changed — see the diagnostic snapshot."
        )

    log("Click sent — checking for consent dialogs…")
    _dismiss_known_consent_dialogs(page, log)

    log("Waiting for editor URL…")
    # Idiomatic Playwright wait. If the same page navigates (most common case),
    # this fires as soon as the URL matches.
    try:
        page.wait_for_url(_EDITOR_URL_RE, timeout=60_000)
        log(f"Editor open: {page.url}")
        return page
    except PlaywrightTimeoutError:
        pass

    # Fallback: editor may have opened in a different tab/window.
    log(f"page.url after timeout: {page.url}")
    all_pages = page.context.pages
    log(f"All pages in context: {[c.url for c in all_pages]}")
    for cand in all_pages:
        try:
            if _EDITOR_URL_RE.search(cand.url):
                log(f"Found editor on alternate page: {cand.url}")
                return cand
        except Exception:
            continue

    raise MyMapsError(
        f"Clicked 'Create a new map' but no editor URL appeared within 60s. "
        f"Current URL: {page.url}. Pages open: {len(all_pages)}."
    )


def _ensure_signed_in(context, page: Page, log: ProgressCallback, timeout_sec: int) -> Page:
    """If we landed on Google's sign-in flow, wait for the user to finish.

    Returns the page we should continue with — possibly different from the
    one passed in, since Google's redirect chain (or popup-based SSO) can
    close the original page and leave a new one as the active editor page.
    """
    if "accounts.google.com" not in page.url and "ServiceLogin" not in page.url:
        return page

    log(f"Sign in to Google in the browser window (waiting up to {timeout_sec}s)…")
    log("⚠ Do NOT close the browser window — the automation continues automatically after sign-in.")

    import time
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        # Look for any open page in this context that's already past the sign-in flow.
        try:
            for candidate in context.pages:
                try:
                    url = candidate.url
                except Exception:
                    continue
                if "google.com/maps/d/" in url:
                    log("Sign-in detected.")
                    return candidate
        except Exception:
            pass
        time.sleep(1.0)

    raise GoogleLoginRequired(
        f"Didn't see a successful Google sign-in within {timeout_sec}s. "
        "Try again — make sure to complete the sign-in in the Edge window "
        "and do NOT close it during the process."
    )


def _wait_for_editor(page: Page, log: ProgressCallback) -> None:
    """Wait until the MyMaps editor chrome is loaded."""
    log("Waiting for the map editor to load…")
    # The map title button (initially 'Untitled map') is a reliable indicator
    page.wait_for_selector("text=/Untitled map|Map title/i", timeout=60_000)


def _set_map_title(page: Page, title: str, log: ProgressCallback) -> None:
    log(f"Setting map title: {title!r}")
    try:
        page.get_by_text(re.compile(r"^Untitled map$", re.IGNORECASE)).first.click(timeout=10_000)
    except PlaywrightTimeoutError:
        # Title may already be set by a previous run — find a "Map title" button instead
        try:
            page.get_by_role("button", name=re.compile(r"map title", re.IGNORECASE)).first.click()
        except PlaywrightTimeoutError:
            log("(Could not click title; continuing without renaming)")
            return

    # Title dialog has a text input — fill it
    try:
        title_input = page.get_by_role("textbox").first
        title_input.fill(title)
        # Find OK / Save button
        for label in ("Save", "OK", "Done"):
            try:
                page.get_by_role("button", name=re.compile(rf"^{label}$", re.IGNORECASE)).click(timeout=3_000)
                break
            except PlaywrightTimeoutError:
                continue
    except PlaywrightTimeoutError:
        log("(Title dialog not found; continuing)")


PICKER_IFRAME_SELECTOR = "iframe[src*='docs.google.com/picker']"


def _import_per_group_layers(
    page: Page,
    request: StudyRequest,
    log: ProgressCallback,
    *,
    run_dir: Optional[Path] = None,
    base_step_idx: int = 0,
) -> None:
    """For each planned study group: build that group's KML, import it into
    a fresh MyMaps layer. First group goes into the default 'Untitled layer';
    each subsequent group gets a new layer via '+ Add layer'.

    MyMaps auto-renames the layer to the KML's <Document><name> on import,
    so we set that to a descriptive name (TUBE Volume, Speed (...) etc.)
    when building each per-group KML.

    Skips groups with zero geocoded locations.
    """
    # Lazy import so mymaps.py doesn't drag in qchub at module-load time.
    from .qchub import _group_locations_for_qchub, _group_layer_name

    groups = _group_locations_for_qchub(request)
    if not groups:
        log("(No study groups planned — nothing to import into MyMaps.)")
        return

    log(f"Planning {len(groups)} layer(s) — one per study group.")
    imported = 0
    for i, g in enumerate(groups, 1):
        layer_name = _group_layer_name(g)
        locs_with_coords = [loc for loc in g["locations"] if loc.estimate is not None]
        if not locs_with_coords:
            log(
                f"  Layer {i}/{len(groups)} ({layer_name!r}): no geocoded locations — "
                "skipping layer creation."
            )
            continue

        kml_result = kml_export.build_kml_for_locations(
            g["locations"], layer_name=layer_name,
        )
        log(
            f"  Layer {i}/{len(groups)} ({layer_name!r}): built KML with "
            f"{kml_result.placemark_count} pin(s)."
        )

        # First non-empty group goes into the existing default 'Untitled layer'.
        # Each subsequent goes into a fresh layer we create.
        if imported > 0:
            # MyMaps' server needs a moment to fully commit the prior layer's
            # state before accepting another import — without this settle,
            # the second import triggers an "error: last action was reverted"
            # toast. History: 5s → 3s (2026-05-15) → 1.5s (2026-05-17 PM,
            # to chase faster inter-group pauses) → 3s (2026-05-17 evening:
            # 1.5s caused real reverts on Group 2 in run-20260517-215952,
            # so we accept the slight pause cost for reliable imports).
            # The other inter-group gains (confirm-scan tightening, Import
            # click timeout drop) still net a much faster overall flow.
            t0 = time.perf_counter()
            log("  Settling 3s before adding next layer (avoids 'last action reverted')…")
            page.wait_for_timeout(3_000)

            log(f"  Clicking '+ Add layer' for group {i}…")
            _click_add_layer(page, log)
            # Wait for the new layer's "Untitled layer" tooltip to actually
            # render — that's the signal it's ready for Import.
            t1 = time.perf_counter()
            try:
                page.locator('[aria-label="Untitled layer"]').first.wait_for(
                    state="visible", timeout=8_000,
                )
            except PlaywrightTimeoutError:
                log("  (didn't see the new 'Untitled layer' aria-label within 8s; proceeding)")
            t2 = time.perf_counter()
            page.wait_for_timeout(250)  # short buffer for Angular wireup
            log(
                f"  Inter-group settle: {t1 - t0:.1f}s fixed + "
                f"{t2 - t1:.1f}s Untitled-layer wait + 0.25s buffer."
            )

        with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
            f.write(kml_result.data)
            tmp_path = Path(f.name)
        try:
            try:
                _import_kmz(
                    page, tmp_path, log,
                    run_dir=run_dir,
                    base_step_idx=base_step_idx + i,
                    expected_layer_name=layer_name,
                )
            except MyMapsError as exc:
                # Retry-once-on-revert: Google MyMaps occasionally rejects
                # the second-or-later layer's import with "An error
                # occurred, your last action was reverted." even with a
                # 9+ second pre-import settle. The empty Untitled layer
                # is still there waiting for an Import — wait longer
                # (the prior layer's server-side commit usually finishes
                # within ~20s), dismiss the toast, then re-fire the
                # import on the same layer. Only retry on revert-class
                # errors (not on real failures like missing file or
                # auth issues).
                msg = str(exc).lower()
                is_revert = ("revert" in msg) or ("last action" in msg) or ("try again" in msg)
                if i == 1 or not is_revert:
                    raise
                log(
                    f"⚠ Layer {i} import was reverted by MyMaps — dismissing the "
                    f"error toast, waiting 20s for server settle, and retrying once."
                )
                _dismiss_mymaps_error_toast(page, log)
                page.wait_for_timeout(20_000)
                _import_kmz(
                    page, tmp_path, log,
                    run_dir=run_dir,
                    base_step_idx=base_step_idx + i,
                    expected_layer_name=layer_name,
                )
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        imported += 1

    if imported == 0:
        # All groups were empty — surface as an error so the user knows the
        # map will be blank rather than getting a silent success.
        raise MyMapsError(
            "No layers imported — none of the planned study groups had "
            "geocoded locations. Fix coordinates in the Locations tab and retry."
        )


def _dismiss_mymaps_error_toast(page: Page, log: ProgressCallback) -> None:
    """Click the 'Dismiss' link on MyMaps' "An error occurred, your last
    action was reverted." toast so it stops blocking subsequent clicks.

    Best-effort; silent on miss. The toast auto-dismisses after a few
    seconds anyway, but actively dismissing tightens the retry window.
    Used by `_import_per_group_layers` when a revert-class error is
    caught and we're about to retry the import.
    """
    try:
        # The toast renders as a Material snackbar with action buttons.
        # "Dismiss" is typically the trailing link/button.
        candidates = [
            page.get_by_role("button", name=re.compile(r"^\s*dismiss\s*$", re.IGNORECASE)).first,
            page.get_by_text("Dismiss", exact=True).first,
        ]
        for cand in candidates:
            try:
                if cand.is_visible(timeout=500):
                    cand.click(timeout=2_000)
                    log("  Dismissed MyMaps error toast.")
                    return
            except Exception:
                continue
    except Exception:
        pass


def _click_add_layer(page: Page, log: ProgressCallback) -> None:
    """Click MyMaps' '+ Add layer' button.

    The control is in the layers panel header. Selector strategy: aria-label
    'Add layer' first, then visible text 'Add layer' as fallback. After
    clicking, a new 'Untitled layer' appears at the top of the panel.
    """
    try:
        page.get_by_role("button", name=re.compile(r"add\s*layer", re.IGNORECASE)).first.click(timeout=5_000)
        return
    except PlaywrightTimeoutError:
        pass
    try:
        page.get_by_text("Add layer", exact=True).first.click(timeout=5_000)
        return
    except PlaywrightTimeoutError:
        pass
    # Last-resort: aria-label match
    try:
        page.locator('[aria-label*="Add layer" i]').first.click(timeout=5_000)
    except Exception as exc:
        raise MyMapsError(f"Couldn't find or click the '+ Add layer' control: {exc}")


def _import_kmz(
    page: Page,
    kmz_path: Path,
    log: ProgressCallback,
    *,
    run_dir: Optional[Path] = None,
    base_step_idx: int = 0,
    expected_layer_name: Optional[str] = None,
    render_timeout_sec: int = 90,
) -> None:
    """Click Import in the active (newest) layer, upload `kmz_path`, and wait
    for the import to render.

    If `expected_layer_name` is provided, verify the layer is RENAMED to that
    name (positive signal — MyMaps auto-renames the layer to the KML's
    <Document><name> on successful import). This replaces an older false-
    positive verification that watched for `[aria-label="Untitled layer"]`
    to hide — observed in run-20260514-174928 (the 28-pin tube approach
    layer never actually rendered, but our wait silently passed because
    the fallback "Importing..." check returned hidden=True for an element
    that was never visible).

    The positive check fires AS SOON AS the rename happens (usually a few
    seconds for typical imports), so the timeout is just a safety ceiling
    — real elapsed time should be much shorter. If the rename doesn't
    happen within `render_timeout_sec`, we raise MyMapsError instead of
    swallowing the failure.
    """
    sub_idx = 0

    def snap(tag: str) -> None:
        nonlocal sub_idx
        if run_dir is None:
            return
        sub_idx += 1
        _save_step_snapshot(page, run_dir, base_step_idx, f"import-kmz-{sub_idx:02d}-{tag}")

    # Log KMZ size up-front so a 0-byte / empty file is obvious in diagnostics.
    try:
        kmz_bytes = kmz_path.stat().st_size
        log(f"KMZ to upload: {kmz_path.name} ({kmz_bytes} bytes)")
    except Exception:
        kmz_bytes = -1

    log("Clicking Import in the active (newest) layer…")
    # MyMaps renders one 'Import' affordance per layer card. The Import
    # for a populated layer is STILL IN THE DOM after that layer is filled,
    # so `.first` picks the stale Layer-1 Import on subsequent iterations
    # (observed 2026-05-14 run-173102 → order 176406 MyMaps run failed at
    # Group 2 because Playwright timed out clicking the stale Layer-1
    # Import). MyMaps appends new layers LATER in document order, so
    # `.last` reliably picks the newest layer's Import — works for both
    # the single-layer case (one match) and the multi-layer case
    # (Layer N's Import is the most recent in DOM).
    #
    # Primary-click timeout dropped 8s → 3s on 2026-05-17 to reduce
    # observed inter-group pauses. The fallback chain catches the rare
    # case where the primary selector times out.
    import_aria = "Import data from a CSV file, spreadsheet or KML."
    clicked = False
    t_click_start = time.perf_counter()
    try:
        page.locator(f'[aria-label="{import_aria}"]:visible').last.click(timeout=3_000)
        clicked = True
    except PlaywrightTimeoutError:
        pass
    if not clicked:
        # Fallback 1: drop the :visible filter.
        try:
            page.locator(f'[aria-label="{import_aria}"]').last.click(timeout=3_000)
            clicked = True
        except PlaywrightTimeoutError:
            pass
    if not clicked:
        # Fallback 2: text-based, last match in document order.
        try:
            page.get_by_text("Import", exact=True).last.click(timeout=3_000)
            clicked = True
        except Exception:
            pass
    if not clicked:
        raise MyMapsError(
            "Couldn't find a clickable 'Import' control in the newest layer card."
        )
    log(f"  (Import click took {time.perf_counter() - t_click_start:.1f}s.)")
    snap("after-import-click")

    # Picker handling, including a one-shot retry of the whole Import flow
    # when Google's picker iframe loads its DOM shell but never renders
    # its content (observed run-20260525-182211: iframe visible but empty
    # white box, then degraded to a cloud-thinking placeholder — Browse
    # button never appeared, we bailed after 16s of patient locator
    # retries). The retry kills any open picker via Escape + re-clicks
    # Import to ask Google for a fresh picker instance.
    def _open_picker_and_wait_for_content() -> "FrameLocator":  # noqa: F821
        log("Waiting for upload picker iframe…")
        t_iframe_start = time.perf_counter()
        try:
            page.wait_for_selector(PICKER_IFRAME_SELECTOR, timeout=15_000)
        except PlaywrightTimeoutError:
            raise MyMapsError("Import picker iframe did not appear after clicking Import.")
        log(f"  (Picker iframe appeared in {time.perf_counter() - t_iframe_start:.1f}s.)")
        snap("picker-iframe-visible")

        picker_inner = page.frame_locator(PICKER_IFRAME_SELECTOR)
        # The iframe element being in the DOM is NOT the same as the
        # picker's React app being loaded inside it. Wait for ANY
        # clickable element to appear inside the iframe before declaring
        # the picker ready. Without this, our Browse-button hunt would
        # spin against an empty iframe and never recover.
        t_content_start = time.perf_counter()
        try:
            picker_inner.locator("button, [role='button']").first.wait_for(
                state="visible", timeout=20_000,
            )
        except PlaywrightTimeoutError:
            raise MyMapsError(
                "Picker iframe loaded but its content never rendered "
                f"(no buttons after {time.perf_counter() - t_content_start:.0f}s)."
            )
        log(f"  (Picker content rendered in {time.perf_counter() - t_content_start:.1f}s.)")
        snap("picker-content-rendered")
        return picker_inner

    try:
        picker = _open_picker_and_wait_for_content()
    except MyMapsError as exc:
        log(f"  Picker did not load on first try ({exc}). Retrying once.")
        # Dismiss whatever stalled picker is open + re-trigger Import.
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        page.wait_for_timeout(500)
        try:
            page.locator(f'[aria-label="{import_aria}"]:visible').last.click(timeout=4_000)
        except Exception:
            # Fall back to text-based — same chain as the initial click.
            try:
                page.get_by_text("Import", exact=True).last.click(timeout=4_000)
            except Exception as click_exc:
                raise MyMapsError(
                    f"Picker stalled and the retry Import click also failed: {click_exc}"
                )
        snap("after-import-click-retry")
        picker = _open_picker_and_wait_for_content()

    log("Uploading KMZ via Browse button (inside picker iframe)…")
    browse_patterns = [
        re.compile(r"^\s*browse\s*$", re.IGNORECASE),
        re.compile(r"select a file", re.IGNORECASE),
        re.compile(r"upload", re.IGNORECASE),
    ]
    try:
        with page.expect_file_chooser(timeout=20_000) as fc_info:
            clicked = False
            for pat in browse_patterns:
                try:
                    picker.get_by_role("button", name=pat).first.click(timeout=4_000)
                    clicked = True
                    break
                except Exception:
                    continue
            if not clicked:
                try:
                    picker.get_by_text("Browse", exact=True).first.click(timeout=4_000)
                    clicked = True
                except Exception:
                    pass
            if not clicked:
                raise MyMapsError("Couldn't find Browse button inside picker iframe.")
        fc_info.value.set_files(str(kmz_path))
        snap("after-file-set")
    except PlaywrightTimeoutError as exc:
        raise MyMapsError(f"File chooser never appeared after clicking Browse: {exc}")

    # Confirmation click. The Google Picker doesn't always auto-submit after
    # set_files — especially on the SECOND+ import in the same session, where
    # it sits with a full progress bar but waits for explicit confirmation.
    # Observed twice (2026-05-15 run-100111, run-102453): group 1 auto-submits
    # in ~6s, group 2 hangs at the "ready" state and times out.
    #
    # Approach:
    #   1. Wait briefly for the picker's post-upload UI to render.
    #   2. Try a wide net of button name patterns (modern picker varies).
    #   3. If still nothing, enumerate the picker's visible buttons and
    #      log them — that gives the NEXT diagnostic real data on what's
    #      actually there.
    #   4. As a last resort, press Enter on the picker (some versions accept
    #      Enter as submit on the upload tab).
    # Confirmation strategy, in priority order:
    #   1. Tiny settle for the picker to react to the upload.
    #   2. If the picker iframe is already GONE — Google auto-submitted on
    #      our `set_files` (the common path observed 2026-05-17). Skip the
    #      whole scan. This alone saves ~17s per upload in the auto-submit
    #      case (the prior 11×1.5s pattern scan burned its full budget
    #      finding nothing, since the iframe had no buttons left to match).
    #   3. Else scan a narrowed set of button name patterns with a SHORT
    #      per-pattern timeout. The full picker-iframe absence check has
    #      already ruled out the common case; what's left is the rare
    #      "picker still up, waiting for explicit confirm" path observed
    #      pre-2026-05-17.
    page.wait_for_timeout(400)
    confirmed_click = False
    iframe_gone_early = False
    try:
        if page.locator(PICKER_IFRAME_SELECTOR).count() == 0:
            log("Picker iframe auto-dismissed after upload — no confirm click needed.")
            iframe_gone_early = True
            confirmed_click = True  # treat as success path
    except Exception:
        pass

    if not iframe_gone_early:
        # Pre-scan: if the picker iframe is technically present but has
        # ZERO visible buttons inside, treat that as auto-submit too.
        # Observed in run-20260517-215952: iframe element persisted post-
        # upload but its contents were cleared, so the scan-then-find-
        # nothing pattern still burned its full budget (~5s). Skipping
        # the scan saves that time.
        try:
            visible_btn_count = picker.locator("button:visible").count()
            if visible_btn_count == 0:
                log("Picker iframe has 0 visible buttons — treating as auto-submit (post-upload cleared state).")
                confirmed_click = True
        except Exception:
            pass

    if not confirmed_click:
        # Patterns ordered by observed frequency. Per-pattern timeout
        # dropped 1.5s → 0.4s; if a button is going to be there, it's
        # rendered by the time we get here. With 9 patterns × 0.4s = 3.6s
        # worst case (was 16.5s).
        confirm_patterns = [
            re.compile(r"^\s*select\s*$", re.IGNORECASE),
            re.compile(r"^\s*open\s*$", re.IGNORECASE),
            re.compile(r"^\s*upload\s*$", re.IGNORECASE),
            re.compile(r"^\s*done\s*$", re.IGNORECASE),
            re.compile(r"^\s*insert\s*$", re.IGNORECASE),
            re.compile(r"^\s*confirm\s*$", re.IGNORECASE),
            re.compile(r"^\s*continue\s*$", re.IGNORECASE),
            re.compile(r"^\s*ok\s*$", re.IGNORECASE),
            re.compile(r"^\s*add\s*$", re.IGNORECASE),
        ]
        for pat in confirm_patterns:
            try:
                picker.get_by_role("button", name=pat).first.click(timeout=400)
                log(f"Confirmed file selection (matched button '{pat.pattern}').")
                confirmed_click = True
                break
            except Exception:
                # Between patterns, re-check whether the iframe has gone
                # away — picker may have auto-submitted while we were
                # scanning. Short-circuit to avoid burning the rest of the
                # budget on a no-op iframe.
                try:
                    if page.locator(PICKER_IFRAME_SELECTOR).count() == 0:
                        log("Picker iframe disappeared mid-scan — treating as auto-submit.")
                        confirmed_click = True
                        break
                except Exception:
                    pass
                continue
        if not confirmed_click:
            # No button matched AND iframe still present — log what's
            # actually there for diagnostics and fall back to Enter.
            try:
                visible_btns = picker.locator("button:visible").all_text_contents()
                visible_btns = [b.strip() for b in visible_btns if b.strip()]
                log(
                    f"(No confirm button matched in {len(confirm_patterns)} patterns. "
                    f"Picker has {len(visible_btns)} visible button(s): "
                    f"{visible_btns[:20]}{'…' if len(visible_btns) > 20 else ''})"
                )
            except Exception as exc:
                log(f"(Couldn't enumerate picker buttons: {exc})")
            try:
                page.keyboard.press("Enter")
                log("(Pressed Enter on picker as last-resort confirm.)")
            except Exception:
                pass

    # PRIMARY completion signal: the layer label flips from 'Untitled layer'
    # to the KMZ's document name. This fires once the server has processed
    # the upload and rendered pins on the map. We use POSITIVE verification
    # — wait for the EXPECTED LAYER NAME to appear in a `data-tooltip`
    # attribute — when the caller passes one. The old approach (waiting for
    # `[aria-label="Untitled layer"]` to be hidden, with an "Importing…"
    # fallback) gave false positives: the "Importing…" text often was never
    # visible to begin with, so the wait returned hidden=True instantly,
    # marking a never-rendered layer as "success" (observed 2026-05-14
    # run-174928: the 28-pin tube approach layer silently fell through this
    # check and the map shipped without that layer's pins).
    log_msg = (
        f"Waiting for layer to render (rename to {expected_layer_name!r}, up to {render_timeout_sec}s)…"
        if expected_layer_name
        else f"Waiting for layer to render (Untitled layer → KMZ name, up to {render_timeout_sec}s)…"
    )
    log(log_msg)
    import_completed = False

    detected_error: Optional[str] = None
    if expected_layer_name:
        # POSITIVE verification: look for the renamed layer's tooltip.
        # MyMaps surfaces the layer's name in `data-tooltip` on the layer
        # card header. Race this against any MyMaps error/snackbar — if
        # the import is rejected ("error, last action reverted"), the
        # snackbar appears transiently and we want to fail loudly with
        # that message rather than wait the full timeout.
        try:
            result = page.wait_for_function(
                """(expected) => {
                    // Success: the expected layer name appeared as a tooltip
                    const renamed = document.querySelector(`[data-tooltip="${expected}"]`);
                    if (renamed) return { state: 'success' };
                    // Failure: any visible error/snackbar from MyMaps. We probe a
                    // few common selectors — Material snackbar, role=alert, and
                    // text-content patterns that MyMaps uses for revert errors.
                    const errorSelectors = [
                        '[role="alert"]',
                        '.snackbar-toast',
                        '.mdc-snackbar',
                        '.gb_4a', '.gb_5a',  // Google's generic notification classes
                    ];
                    for (const sel of errorSelectors) {
                        const els = Array.from(document.querySelectorAll(sel));
                        for (const el of els) {
                            const txt = (el.innerText || '').trim();
                            if (!txt) continue;
                            // Only treat as error if visible AND text smells like a failure
                            if (el.offsetParent === null) continue;
                            if (/revert|could not|cannot|error|failed|unable|try again/i.test(txt)) {
                                return { state: 'error', message: txt.slice(0, 300) };
                            }
                        }
                    }
                    // Aria-live announcer (MyMaps' a11y status region) often
                    // gets the error text injected briefly even when the
                    // visual toast has its own selector.
                    const liveRegions = document.querySelectorAll('[aria-live]');
                    for (const lr of liveRegions) {
                        const txt = (lr.innerText || lr.textContent || '').trim();
                        if (!txt) continue;
                        if (/revert|could not|cannot|error|failed|unable|try again/i.test(txt)) {
                            return { state: 'error', message: txt.slice(0, 300) };
                        }
                    }
                    return null;  // keep polling
                }""",
                arg=expected_layer_name,
                timeout=render_timeout_sec * 1000,
                polling=500,  # check every 500ms
            )
            try:
                payload = result.json_value()
            except Exception:
                payload = None
            if isinstance(payload, dict):
                if payload.get("state") == "success":
                    import_completed = True
                elif payload.get("state") == "error":
                    detected_error = str(payload.get("message", "")).strip() or "unknown MyMaps error"
        except PlaywrightTimeoutError:
            pass
    else:
        # Legacy path for callers that don't know the expected name.
        try:
            page.locator('[aria-label="Untitled layer"]').wait_for(
                state="hidden", timeout=render_timeout_sec * 1000
            )
            import_completed = True
        except PlaywrightTimeoutError:
            pass

    snap("after-layer-render-check")

    if not import_completed:
        if detected_error:
            # MyMaps surfaced its own error message during the wait — pass it
            # through verbatim instead of our generic timeout message. This
            # is the "error, the last action was reverted" case observed in
            # run-20260514-214550.
            raise MyMapsError(
                f"MyMaps rejected the import for layer "
                f"{expected_layer_name!r}: {detected_error}. "
                "Likely cause: the server hadn't fully committed the prior "
                "layer's state. We added a settle delay between imports, but "
                "the underlying upload still bounced. KMZ size: "
                f"{kmz_bytes} bytes. Check the import-kmz-* snapshots."
            )
        detail = (
            f"layer never renamed to {expected_layer_name!r}"
            if expected_layer_name
            else "'Untitled layer' label never became hidden"
        )
        raise MyMapsError(
            f"MyMaps import didn't complete within {render_timeout_sec}s — {detail}. "
            f"The KMZ was likely rejected, the import errored mid-flight, or MyMaps was "
            f"unusually slow. KMZ size: {kmz_bytes} bytes. Confirm-button clicked: "
            f"{confirmed_click}. Check the import-kmz-* snapshots in the run directory."
        )


def _open_share_dialog(page: Page, log: ProgressCallback) -> None:
    log("Opening Share dialog…")
    # Map-action items are <li id="map-action-share">, not <button>.
    for locator in (page.locator("#map-action-share"), page.get_by_text("Share", exact=True)):
        try:
            locator.first.click(timeout=8_000)
            # Give the dialog a moment to render, then dismiss any Drive consent
            # popup that might appear (sharing for the first time can trigger one).
            page.wait_for_timeout(1_500)
            _dismiss_known_consent_dialogs(page, log)
            return
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    raise MyMapsError("Couldn't open the Share dialog.")


def _capture_share_link(page: Page, log: ProgressCallback) -> Optional[str]:
    """Try to switch to 'anyone with the link' and capture the share URL.

    Performance note: the viewer URL is a deterministic transform of the
    editor URL (`mid=<id>` appears in both), so we derive it synchronously
    up-front. The DOM-input read used to take ~5s when it timed out — we
    just skip it now. The access-level flip is still attempted (team
    sharing depends on the map being public), but with tighter timeouts
    so the silent-failure path doesn't sink 10s+ on every run.
    """
    # Derive the viewer URL first — pure URL regex, ~0ms, no DOM. This is
    # the canonical share URL we'll return regardless of how the access-
    # flip goes.
    share_url = _derive_share_url_from_edit(page.url)

    log("Setting link sharing to 'anyone with the link'…")
    try:
        page.get_by_text(re.compile(r"restricted", re.IGNORECASE)).first.click(timeout=2_500)
        page.get_by_text(re.compile(r"anyone with the link", re.IGNORECASE)).first.click(timeout=2_500)
    except PlaywrightTimeoutError:
        try:
            page.get_by_role("button", name=re.compile(r"change", re.IGNORECASE)).first.click(timeout=2_000)
            page.get_by_text(re.compile(r"anyone with the link", re.IGNORECASE)).first.click(timeout=2_500)
        except PlaywrightTimeoutError:
            log("(Couldn't switch access level — may already be public, or layout differs)")

    for label in ("Done", "Close"):
        try:
            page.get_by_role("button", name=re.compile(rf"^{label}$", re.IGNORECASE)).first.click(timeout=1_000)
            break
        except PlaywrightTimeoutError:
            continue

    return share_url


def _derive_share_url_from_edit(edit_url: str) -> Optional[str]:
    """Derive a viewer URL from the editable URL.

    Edit URL:   https://www.google.com/maps/d/u/0/edit?mid=<id>&...
    Viewer URL: https://www.google.com/maps/d/viewer?mid=<id>
    """
    m = re.search(r"mid=([^&]+)", edit_url)
    if not m:
        return None
    return f"https://www.google.com/maps/d/viewer?mid={m.group(1)}"
