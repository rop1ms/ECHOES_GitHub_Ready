"""Small UI batching helpers; no polling or work on the input path."""
from functools import wraps


def batch_updates(*names, on_error=None):
    """Coalesce paints while replacing content; restore state even after a failure."""
    def decorate(fn):
        @wraps(fn)
        def run(self, *args, **kwargs):
            widgets = []
            for name in names:
                widget = getattr(self, name, None)
                if widget is not None and widget.updatesEnabled():
                    widget.setUpdatesEnabled(False)
                    widgets.append(widget)
            try:
                return fn(self, *args, **kwargs)
            except Exception:
                cleanup = getattr(self, on_error, None) if on_error else None
                if cleanup is not None:
                    cleanup()
                raise
            finally:
                for widget in reversed(widgets):
                    widget.setUpdatesEnabled(True)
        return run
    return decorate
