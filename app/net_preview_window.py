"""Resizable preview windows, kept outside the compact in-app dialog wrapper."""
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import QDialog


class PreviewDialog(QDialog):
    """A real desktop window: resize at any edge, maximize, or use fullscreen.

    The regular in-app dialog adapter is useful for forms, but it reparents the
    dialog into a scroll area and loses the media viewport's requested size.
    Previews retain their own window so their content can use the whole screen.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowSystemMenuHint |
                            Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
        self.setSizeGripEnabled(True)
        screen = parent.screen() if parent is not None else self.screen()
        if screen is not None:
            area = screen.availableGeometry()
            self.setMinimumSize(QSize(min(540, area.width()), min(360, area.height())))
            width, height = max(540, int(area.width() * .88)), max(360, int(area.height() * .88))
            self.resize(min(width, area.width()), min(height, area.height()))
            self.move(area.center() - self.rect().center())
        else:
            self.setMinimumSize(540, 360)
            self.resize(1120, 760)
        self._before_fullscreen = Qt.WindowState.WindowNoState

    def exec(self):
        # inapp.install() replaces QDialog.exec process-wide. Use its saved Qt
        # method only for this media window, leaving other dialogs unchanged.
        import inapp
        native_exec = inapp._orig.get("exec", QDialog.exec)
        return native_exec(self)

    def toggle_maximized(self):
        if self.isFullScreen():
            self.toggle_fullscreen()
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.setWindowState(self._before_fullscreen)
            self.show()
        else:
            self._before_fullscreen = self.windowState()
            self.showFullScreen()

