"""In-place UI language changes. Widget state, drafts and playback are retained."""
import weakref

_WIDGETS=weakref.WeakSet()
NATIVE={}
_INSTALLED=False

class UIText(str):
    def __new__(cls,ru,en,language):
        obj=str.__new__(cls,en if language=='en' else ru)
        obj._echoes_ui_pair=(ru,en);return obj

def render(source):
    import i18n
    if isinstance(source,(list,tuple)):return [render(item) for item in source]
    return i18n.tr(source)

def record(widget,name,source,setter,args=(),display=None):
    import i18n
    fields=getattr(widget,'_echoes_lang_fields',None)
    tracked=isinstance(source,(list,tuple)) or isinstance(source,str) and (hasattr(source,'_echoes_ui_pair') or i18n._CYR.search(source))
    if tracked:
        if fields is None:fields=widget._echoes_lang_fields={}
        fields[name]=[source,setter,args,render(source) if display is None else display]
        _WIDGETS.add(widget)
    elif fields is not None:fields.pop(name,None)

def source_text(widget,setter_name,display):
    entry=getattr(widget,'_echoes_lang_fields',{}).get(setter_name)
    if entry and display==entry[3]:
        source=entry[0]
        return render(source) if hasattr(source,'_echoes_ui_pair') else source
    return None

def _native(cls,name):
    return next((NATIVE[(base,name)] for base in cls.__mro__ if (base,name) in NATIVE),getattr(cls,name))

def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True
    from PyQt6 import QtWidgets as W,QtGui as G
    import i18n
    # Constructors implemented in C++ bypass Python's setText wrapper.
    for cls,setter in [(W.QLabel,'setText'),(W.QPushButton,'setText'),(W.QCheckBox,'setText'),
        (W.QRadioButton,'setText'),(W.QGroupBox,'setTitle'),(W.QMenu,'setTitle'),(G.QAction,'setText')]:
        original=cls.__init__;native=_native(cls,setter)
        def init(widget,*args,_init=original,_setter=setter,_native=native,**kwargs):
            original_text=next((arg for arg in args if isinstance(arg,str)),None)
            args=tuple(render(arg) if isinstance(arg,UIText) else arg for arg in args)
            _init(widget,*args,**kwargs)
            if original_text is not None:
                translated=render(original_text)
                if _setter=='setText' and isinstance(widget,(W.QAbstractButton,G.QAction)):translated=i18n._amp(str(original_text),translated)
                record(widget,_setter,original_text,_native,display=translated)
                if translated!=original_text or isinstance(original_text,UIText):_native(widget,translated)
        cls.__init__=init

    C=W.QComboBox
    add,insert,set_text,clear,remove=C.addItem,C.insertItem,C.setItemText,C.clear,C.removeItem
    native_item=i18n._orig_setItemText
    def combo_source(widget,index,source):
        data=getattr(widget,'_echoes_combo_sources',None)
        if data is None:data=widget._echoes_combo_sources={}
        data[index]=source;_WIDGETS.add(widget)
    def add_item(widget,*args):
        result=add(widget,*args);source=next((arg for arg in args[:2] if isinstance(arg,str)),None)
        if source is not None:combo_source(widget,widget.count()-1,source)
        return result
    def insert_item(widget,index,*args):
        index=max(0,min(index,widget.count()))
        widget._echoes_combo_sources={i+(i>=index):s for i,s in getattr(widget,'_echoes_combo_sources',{}).items()}
        result=insert(widget,index,*args);source=next((a for a in args[:2] if isinstance(a,str)),None)
        if source is not None:combo_source(widget,index,source)
        return result
    def set_item(widget,index,source):
        result=set_text(widget,index,source);combo_source(widget,index,source);return result
    def clear_items(widget):
        widget._echoes_combo_sources={};return clear(widget)
    def remove_item(widget,index):
        widget._echoes_combo_sources={i-(i>index):s for i,s in getattr(widget,'_echoes_combo_sources',{}).items() if i!=index}
        return remove(widget,index)
    C.addItem=add_item;C.insertItem=insert_item;C.setItemText=set_item;C.clear=clear_items;C.removeItem=remove_item
    def add_items(widget,items):
        for item in items:add_item(widget,item)
    C.addItems=add_items
    old_item_text,old_current_text=C.itemText,C.currentText
    def item_text(widget,index):
        source=getattr(widget,'_echoes_combo_sources',{}).get(index)
        if source is None:return old_item_text(widget,index)
        return render(source) if isinstance(source,UIText) else source
    def current_text(widget):
        source=getattr(widget,'_echoes_combo_sources',{}).get(widget.currentIndex())
        if source is not None and (not widget.isEditable() or widget.lineEdit().text()==render(source)):
            return render(source) if isinstance(source,UIText) else source
        return old_current_text(widget)
    C.itemText=item_text;C.currentText=current_text

    T=W.QTabWidget
    add_tab,insert_tab,set_tab=T.addTab,T.insertTab,T.setTabText
    def tab_source(widget,page,source):
        data=getattr(widget,'_echoes_tab_sources',None)
        if data is None:data=widget._echoes_tab_sources=weakref.WeakKeyDictionary()
        data[page]=source;_WIDGETS.add(widget)
    def add_tab_text(widget,page,*args):
        index=add_tab(widget,page,*args);source=next((a for a in args if isinstance(a,str)),None)
        if source is not None:tab_source(widget,page,source)
        return index
    def insert_tab_text(widget,index,page,*args):
        result=insert_tab(widget,index,page,*args);source=next((a for a in args if isinstance(a,str)),None)
        if source is not None:tab_source(widget,page,source)
        return result
    def set_tab_text(widget,index,source):
        result=set_tab(widget,index,source)
        if widget.widget(index) is not None:tab_source(widget,widget.widget(index),source)
        return result
    T.addTab=add_tab_text;T.insertTab=insert_tab_text;T.setTabText=set_tab_text

    M=W.QMenu;add_action=M.addAction
    def menu_action(widget,*args):
        action=add_action(widget,*args);source=next((a for a in args[:2] if isinstance(a,str)),None)
        if action is not None and source is not None:
            record(action,'setText',source,i18n._orig_action_setText,display=i18n._amp(str(source),render(source)))
        return action
    M.addAction=menu_action
    for name in ('addSection','addMenu'):
        original=getattr(M,name)
        def menu_entry(widget,*args,_original=original):
            result=_original(widget,*args)
            source=next((a for a in args[:2] if isinstance(a,str)),None)
            if source is not None and result is not None:
                action=result.menuAction() if isinstance(result,M) else result
                record(action,'setText',source,i18n._orig_action_setText,display=i18n._amp(str(source),render(source)))
                if isinstance(result,M):record(result,'setTitle',source,_native(M,'setTitle'))
            return result
        setattr(M,name,menu_entry)
    for cls,name in [(W.QTreeWidget,'setHeaderLabels'),(W.QTableWidget,'setHorizontalHeaderLabels'),(W.QTableWidget,'setVerticalHeaderLabels')]:
        original=getattr(cls,name)
        def headers(widget,labels,_original=original,_name=name):
            values=list(labels);result=_original(widget,values);record(widget,_name,values,_original);return result
        setattr(cls,name,headers)

def apply(language,window=None):
    import i18n
    from PyQt6 import sip
    from PyQt6 import QtWidgets as W
    from PyQt6.QtGui import QAction
    from PyQt6.QtCore import QEvent,QCoreApplication
    app=W.QApplication.instance()
    if app is None:return
    # Collect any remaining constructor captions before changing the catalogue.
    i18n.sweep()
    drafts=[]
    for widget in list(_WIDGETS):
        if not sip.isdeleted(widget) and isinstance(widget,W.QComboBox) and widget.isEditable():
            editor=widget.lineEdit();index=widget.model().index(widget.currentIndex(),widget.modelColumn())
            if editor.text()!=widget.model().data(index):drafts.append((widget,editor.text(),editor.cursorPosition()))
    i18n.install(language)
    import ui_anim
    for overlay in list(ui_anim._OVERLAYS.values()):
        if not sip.isdeleted(overlay):overlay.finish()
    for widget in list(_WIDGETS):
        if sip.isdeleted(widget):continue
        blocked=widget.blockSignals(True)
        try:
            for name,entry in list(getattr(widget,'_echoes_lang_fields',{}).items()):
                source,setter,args,_=entry
                if name=='setHtml' and hasattr(widget,'isReadOnly') and not widget.isReadOnly():continue
                translated=render(source)
                if name=='setText' and isinstance(widget,(W.QAbstractButton,QAction)):
                    translated=i18n._amp(str(source),translated)
                setter(widget,translated,*args);entry[3]=translated
            if isinstance(widget,W.QComboBox):
                for index,source in getattr(widget,'_echoes_combo_sources',{}).items():
                    if 0<=index<widget.count():i18n._orig_setItemText(widget,index,render(source))
            if isinstance(widget,W.QTabWidget):
                for page,source in list(getattr(widget,'_echoes_tab_sources',{}).items()):
                    if not sip.isdeleted(page) and widget.indexOf(page)>=0:widget.setTabText(widget.indexOf(page),source)
        except RuntimeError:pass
        finally:
            if not sip.isdeleted(widget):widget.blockSignals(blocked)
    # Text-bearing raster caches must not retain captions from the old language.
    import paint_cache
    from raster_budget import ROWS
    paint_cache.clear_all();ROWS.clear()
    # Card rows painted in C++ (echoes_ui.dll) keep their captions there.
    import sys
    theme=sys.modules.get('yamusic_theme')
    for row in list(getattr(theme,'_NATIVE_ROWS',())):row.relabel()
    import native_spoken
    native_spoken.relabel(language)
    for widget in app.allWidgets():
        if sip.isdeleted(widget):continue
        if isinstance(widget,W.QListWidget):
            for index in range(widget.count()):
                item=widget.item(index);source=getattr(item,'_i18n_list_src',None)
                if source is not None and item.data(256) is None:item.setText(render(source))
        if isinstance(widget,W.QTableWidget):
            for row in range(widget.rowCount()):
                for col in range(widget.columnCount()):
                    item=widget.item(row,col);source=getattr(item,'_i18n',None)
                    if isinstance(source,str):item.setText(source)
        if isinstance(widget,W.QTreeWidget):
            pending=[widget.topLevelItem(i) for i in range(widget.topLevelItemCount())]
            while pending:
                item=pending.pop()
                for col,source in list(getattr(item,'_i18n',{}).items()):item.setText(col,source)
                pending.extend(item.child(i) for i in range(item.childCount()))
        QCoreApplication.sendEvent(widget,QEvent(QEvent.Type.LanguageChange))
        widget.update()
    for widget,text,cursor in drafts:
        if not sip.isdeleted(widget):
            editor=widget.lineEdit();blocked=editor.blockSignals(True)
            editor.setText(text);editor.setCursorPosition(cursor);editor.blockSignals(blocked)
