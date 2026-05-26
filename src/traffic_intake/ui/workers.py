"""Background workers — keep heavy operations off the UI thread."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..chat import DEFAULT_MODEL_PREFERENCE, resolve_model_chain, run_chat_turn
from ..email_draft import DraftResult, EmailDraftError, draft_reply
from ..extractor import extract
from ..models import StudyRequest
from ..mymaps import CreateMapResult, MyMapsError, create_mymaps_map
from ..parser import parse_email_file, prepare_for_extraction
from ..qchub import (
    CreateOrderResult,
    QchubCompanyNotFound,
    QchubError,
    QchubUserNotFound,
    create_qchub_order,
)


class ExtractionSignals(QObject):
    started = Signal(str)  # filename label
    progress = Signal(str)  # human-readable status line
    finished = Signal(object)  # StudyRequest
    failed = Signal(str)  # error message


class ExtractionWorker(QRunnable):
    """Parse + extract one email in a worker thread."""

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self.signals = ExtractionSignals()

    def run(self) -> None:
        try:
            self.signals.started.emit(self.path.name)
            self.signals.progress.emit("Parsing email…")
            parsed = parse_email_file(self.path)
            prepare_for_extraction(parsed)

            n_img = len(parsed.image_attachments())
            n_kmz = len(parsed.kmz_attachments())
            n_docs = len(parsed.docx_attachments()) + len(parsed.pdf_attachments())
            extra = []
            if n_kmz:
                extra.append(f"{n_kmz} KMZ")
            if n_docs:
                extra.append(f"{n_docs} document{'s' if n_docs != 1 else ''}")
            if n_img:
                extra.append(f"{n_img} aerial image{'s' if n_img != 1 else ''}")
            if parsed.is_forwarded:
                extra.append("forwarded")
            attach_note = f" ({', '.join(extra)})" if extra else ""

            self.signals.progress.emit(f"Sending to Claude for extraction{attach_note}…")
            # `extract` now emits heartbeat progress during the stream
            # (elapsed seconds, retry attempts, model-fallback swaps).
            # Pipe those through the same signal so the chat status bar
            # stops looking frozen during long extractions.
            request: StudyRequest = extract(
                parsed,
                progress=lambda s: self.signals.progress.emit(s),
            )
            self.signals.finished.emit(request)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


def run_extraction(path: Path, *, on_started, on_progress, on_finished, on_failed) -> None:
    """Dispatch an extraction worker onto the global thread pool."""
    worker = ExtractionWorker(path)
    worker.signals.started.connect(on_started)
    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)
    worker.signals.failed.connect(on_failed)
    QThreadPool.globalInstance().start(worker)


class MyMapsSignals(QObject):
    progress = Signal(str)
    finished = Signal(object)  # CreateMapResult
    failed = Signal(str)


class MyMapsWorker(QRunnable):
    """Run the MyMaps Playwright flow off the UI thread."""

    def __init__(self, request: StudyRequest):
        super().__init__()
        self.request = request
        self.signals = MyMapsSignals()

    def run(self) -> None:
        try:
            # Pre-flight: at least one location with coordinates, otherwise the
            # map will be blank. Per-group KMLs are built INSIDE create_mymaps_map
            # (one per study group, mirroring John Goodwin's workflow), so we
            # only need a quick count here, not a full build.
            n_with = sum(1 for loc in self.request.locations if loc.estimate is not None)
            n_total = self.request.total_locations
            if n_with == 0:
                self.signals.failed.emit(
                    f"None of the {n_total} extracted locations have coordinates yet. "
                    "Set lat/lon for each location first (Locations tab or via chat)."
                )
                return

            self.signals.progress.emit(
                f"{n_with}/{n_total} locations geocoded. Starting MyMaps automation "
                "(one layer will be created per study group)…"
            )
            result: CreateMapResult = create_mymaps_map(
                self.request,
                progress=lambda s: self.signals.progress.emit(s),
            )
            self.signals.finished.emit(result)
        except MyMapsError as exc:
            self.signals.failed.emit(f"MyMaps automation failed: {exc}")
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


def run_mymaps_creation(request: StudyRequest, *, on_progress, on_finished, on_failed) -> None:
    worker = MyMapsWorker(request)
    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)
    worker.signals.failed.connect(on_failed)
    QThreadPool.globalInstance().start(worker)


class QchubSignals(QObject):
    progress = Signal(str)
    finished = Signal(object)   # CreateOrderResult
    failed = Signal(str)
    missing_company = Signal(str, str)  # name, domain
    missing_user = Signal(str)


class QchubWorker(QRunnable):
    """Run the qchub Playwright flow off the UI thread."""

    def __init__(self, request: StudyRequest, qc_office: str | None = None):
        super().__init__()
        self.request = request
        self.qc_office = qc_office
        self.signals = QchubSignals()

    def run(self) -> None:
        # Track whether the success path already fired so we don't also emit
        # `failed` after the user's manual-finish window ends with an error.
        ready_emitted = {"v": False}

        def _emit_ready(r: CreateOrderResult) -> None:
            ready_emitted["v"] = True
            self.signals.finished.emit(r)

        try:
            # Pre-flight: at least one location must have coordinates. Per-group
            # KMLs are now built INSIDE create_qchub_order (one per study group),
            # but the count check here gives a fast, clear error before we spin
            # up the browser.
            n_with = sum(1 for loc in self.request.locations if loc.estimate is not None)
            n_total = self.request.total_locations
            if n_with == 0:
                self.signals.failed.emit(
                    f"None of the {n_total} extracted locations have coordinates yet — "
                    "can't build the KML for qchub. Fix lat/lon in the Locations tab "
                    "(or via chat) and retry."
                )
                return

            self.signals.progress.emit(
                f"{n_with}/{n_total} locations geocoded. Starting qchub automation "
                "(per-group KMLs will be built and uploaded one group at a time)…"
            )

            # create_qchub_order invokes on_ready when the modal/groups/KMLs are
            # done, then keeps the browser alive until the user closes it.
            # We DON'T re-emit finished from the return value — on_ready did.
            create_qchub_order(
                self.request,
                qc_office=self.qc_office,
                progress=lambda s: self.signals.progress.emit(s),
                on_ready=_emit_ready,
            )
        except QchubCompanyNotFound as exc:
            if not ready_emitted["v"]:
                self.signals.missing_company.emit(exc.name, exc.domain or "")
        except QchubUserNotFound as exc:
            if not ready_emitted["v"]:
                self.signals.missing_user.emit(exc.email)
        except QchubError as exc:
            if not ready_emitted["v"]:
                self.signals.failed.emit(f"qchub automation failed: {exc}")
        except Exception as exc:
            if not ready_emitted["v"]:
                self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


def run_qchub_creation(
    request: StudyRequest,
    qc_office: str | None,
    *,
    on_progress,
    on_finished,
    on_failed,
    on_missing_company,
    on_missing_user,
) -> None:
    worker = QchubWorker(request, qc_office=qc_office)
    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)
    worker.signals.failed.connect(on_failed)
    worker.signals.missing_company.connect(on_missing_company)
    worker.signals.missing_user.connect(on_missing_user)
    QThreadPool.globalInstance().start(worker)


# ---------- chat ----------

class ChatSignals(QObject):
    textDelta = Signal(str)        # streaming chunk of assistant text
    toolResult = Signal(str, str)  # (tool_name, result string)
    actionRequest = Signal(str, dict)  # (action_name, args) — fires on main thread
    finished = Signal(object)      # new history list
    failed = Signal(str)


class ChatWorker(QRunnable):
    """Run one chat turn off the UI thread. Mutates `state` in place. Action
    tools (create_mymaps_map etc.) bubble up via the `actionRequest` signal —
    the main window connects that to its existing button handlers.

    `qchub_edit_session` is the live bridge to the qchub browser tab kept
    open after order creation (Ship 2). When present, Ellen's
    *_estimate_* tools dispatch through it; when None, those tools
    politely refuse with a clear message.
    """

    def __init__(
        self, user_message: str, history: list, state: StudyRequest, artifacts: dict,
        qchub_edit_session=None,
    ):
        super().__init__()
        self.user_message = user_message
        self.history = history
        self.state = state
        self.artifacts = artifacts
        self.qchub_edit_session = qchub_edit_session
        self.signals = ChatSignals()

    def run(self) -> None:
        try:
            # Chat model is fixed to the DEFAULT_MODEL_PREFERENCE chain
            # ("auto" = Sonnet → Opus → Haiku fallback). User-facing
            # toggle removed 2026-05-26 — "auto" handles Anthropic
            # overload outages correctly and there's no user case for
            # forcing a single model.
            chain = resolve_model_chain(DEFAULT_MODEL_PREFERENCE)

            new_history = run_chat_turn(
                self.user_message,
                self.history,
                self.state,
                on_text_delta=lambda s: self.signals.textDelta.emit(s),
                on_tool_result=lambda name, result: self.signals.toolResult.emit(name, result),
                on_action_request=lambda name, args: self.signals.actionRequest.emit(name, args),
                artifacts=self.artifacts,
                model_chain=chain,
                qchub_edit_session=self.qchub_edit_session,
            )
            self.signals.finished.emit(new_history)
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


def run_chat(
    user_message: str,
    history: list,
    state: StudyRequest,
    artifacts: dict,
    *,
    on_text_delta,
    on_tool_result,
    on_action_request,
    on_finished,
    on_failed,
    qchub_edit_session=None,
) -> None:
    worker = ChatWorker(user_message, history, state, artifacts, qchub_edit_session=qchub_edit_session)
    worker.signals.textDelta.connect(on_text_delta)
    worker.signals.toolResult.connect(on_tool_result)
    worker.signals.actionRequest.connect(on_action_request)
    worker.signals.finished.connect(on_finished)
    worker.signals.failed.connect(on_failed)
    QThreadPool.globalInstance().start(worker)


class DraftEmailSignals(QObject):
    finished = Signal(object)  # DraftResult
    failed = Signal(str)


class DraftEmailWorker(QRunnable):
    """Open an Outlook draft window with the latest estimate PDF attached.

    Runs off the UI thread because the win32com call can take 1-2s on a
    cold Outlook (it may have to spin up the COM server).
    """

    def __init__(
        self, request: StudyRequest, artifacts: dict,
        *, to: str | None = None, cc: str | None = None,
        subject: str | None = None, body_html: str | None = None,
        deployment_schedule: str | None = None, map_url: str | None = None,
    ):
        super().__init__()
        self.request = request
        self.artifacts = artifacts
        self.to = to
        self.cc = cc
        self.subject = subject
        self.body_html = body_html
        self.deployment_schedule = deployment_schedule
        self.map_url = map_url
        self.signals = DraftEmailSignals()

    def run(self) -> None:
        try:
            try:
                import pythoncom  # type: ignore
                pythoncom.CoInitialize()
                _coinit = True
            except Exception:
                _coinit = False
            try:
                result = draft_reply(
                    self.request,
                    artifacts=self.artifacts,
                    to=self.to, cc=self.cc,
                    subject=self.subject, body_html=self.body_html,
                    deployment_schedule=self.deployment_schedule,
                    map_url=self.map_url,
                )
                self.signals.finished.emit(result)
            finally:
                if _coinit:
                    try:
                        import pythoncom  # type: ignore
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass
        except EmailDraftError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")


def run_email_draft(
    request: StudyRequest,
    artifacts: dict,
    *,
    on_finished,
    on_failed,
    to: str | None = None,
    cc: str | None = None,
    subject: str | None = None,
    body_html: str | None = None,
    deployment_schedule: str | None = None,
    map_url: str | None = None,
) -> None:
    worker = DraftEmailWorker(
        request, artifacts,
        to=to, cc=cc, subject=subject, body_html=body_html,
        deployment_schedule=deployment_schedule, map_url=map_url,
    )
    worker.signals.finished.connect(on_finished)
    worker.signals.failed.connect(on_failed)
    QThreadPool.globalInstance().start(worker)
