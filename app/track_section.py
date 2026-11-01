"""Document-height track section with a screen-sized, virtualized QListView."""
from PyQt6.QtCore import Qt,QPoint,QEvent,QTimer
from PyQt6.QtWidgets import QWidget,QAbstractScrollArea,QSizePolicy

class TrackSection(QWidget):
    def __init__(self,view,row_height):
        super().__init__()
        self.view=view;self.row_height=row_height;self.outer=None;self._syncing=False
        self.setObjectName('YaPage');self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        view.setParent(self)
        view.verticalScrollBar().rangeChanged.connect(self._sync)
        self.refresh()

    def refresh(self):
        self.setFixedHeight(max(60,self.view.model_.rowCount()*self.row_height+6))
        self._sync()

    def _bind(self):
        if self.outer is not None:return
        parent=self.parentWidget()
        while parent is not None:
            if isinstance(parent,QAbstractScrollArea):
                self.outer=parent;parent.verticalScrollBar().valueChanged.connect(self._sync)
                parent.viewport().installEventFilter(self);break
            parent=parent.parentWidget()

    def _sync(self,*_):
        if self._syncing:return
        self._bind()
        if self.outer is None:return
        self._syncing=True
        try:
            height=self.outer.viewport().height();top=self.mapTo(self.outer.viewport(),QPoint()).y()
            if top>=height or top+self.height()<=0:
                self.view.hide();return
            margin=self.row_height*2
            start=max(0,((-top-margin)//self.row_height)*self.row_height)
            # Keep the viewport size stable between row boundaries: resizing
            # it for every scroll pixel would trigger another item layout.
            window=min(self.height()-start,((height+2*margin+self.row_height-1)//self.row_height+1)*self.row_height)
            self.view.setGeometry(0,start,self.width(),window)
            if not self.view.isVisible():self.view.show()
            self.view.verticalScrollBar().setValue(start)
        finally:self._syncing=False

    def scroll_to(self,row):
        self._bind()
        if self.outer is None:return
        bar=self.outer.verticalScrollBar();y=self.mapTo(self.outer.viewport(),QPoint()).y()+row*self.row_height
        if y<0:bar.setValue(bar.value()+y)
        elif y+self.row_height>self.outer.viewport().height():
            bar.setValue(bar.value()+y+self.row_height-self.outer.viewport().height())
        self._sync()

    def showEvent(self,event):
        super().showEvent(event);self._sync();QTimer.singleShot(0,self._sync)
    def resizeEvent(self,event):
        super().resizeEvent(event);self._sync()
    def eventFilter(self,obj,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show):self._sync()
        return False
