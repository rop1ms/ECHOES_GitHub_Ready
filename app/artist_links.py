"""Separate keyboard-accessible artist links; URLs never leave the application."""
from html import escape
from PyQt6.QtCore import Qt,pyqtSignal
from PyQt6.QtWidgets import QLabel
from artist_identity import credits

class ArtistLinks(QLabel):
    artist_clicked=pyqtSignal(str)
    def __init__(self,parent=None):
        super().__init__(parent)
        self.names=[]
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse|Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.setOpenExternalLinks(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWordWrap(True)
        self.linkActivated.connect(self._activate)
    def set_track(self,track):
        self.names=credits(track or {})
        self.setText(' <span style="color:#888888">·</span> '.join(
            f'<a href="artist:{i}" style="color:#bcbcbc;text-decoration:none">{escape(n)}</a>'
            for i,n in enumerate(self.names)))
        self.setToolTip('Открыть профиль: '+', '.join(self.names) if self.names else '')
        self.setAccessibleName('Исполнители: '+', '.join(self.names))
    def _activate(self,link):
        if not link.startswith('artist:'):return
        try:i=int(link[7:])
        except ValueError:return
        if 0<=i<len(self.names):self.artist_clicked.emit(self.names[i])
