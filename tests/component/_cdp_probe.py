"""Thread-safe launch probe for deterministic fixture failure tests."""

from threading import Event, get_ident


class LaunchProbe:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.cleaned = Event()
        self.launch_thread: int | None = None
        self.cleanup_calls: list[tuple[str, int]] = []

    def launch(self, *_args, **_kwargs):
        self.launch_thread = get_ident()
        self.started.set()
        assert self.release.wait(5), "Test did not release the delayed launch"
        return self, self

    @property
    def process_id(self) -> int:
        return 123

    def wait_for_url(self, url: str, *, timeout: float) -> None:
        assert get_ident() == self.launch_thread

    def close(self) -> None:
        self.cleanup_calls.append(("close", get_ident()))

    def kill(self) -> None:
        self.cleanup_calls.append(("kill", get_ident()))
        self.cleaned.set()
