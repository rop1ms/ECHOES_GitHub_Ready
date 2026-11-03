"""Liquid-glass report composer, private sent history and staff inbox."""
from __future__ import annotations
import json
from pathlib import Path
import time
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,
    QPlainTextEdit,QComboBox,QPushButton,QTabWidget,QWidget,QListWidget,QSplitter)
from echo_glass import GlassFrame,glass_controls_qss
from duel_settings import tr
import net_bugs


class BugReportsDialog(QDialog):
    def __init__(self,page,embedded=False):
        super().__init__(page.win)
        self.page=page;self.win=page.win;self.node=page._ensure_node(start=False)
        if embedded:self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle(self.t('Запросы и баги','Requests and bugs'));self.resize(850,740);self.setMinimumSize(580,540);self.setSizeGripEnabled(not embedded)
        self.setStyleSheet('QDialog{background:#080b10;}QLabel{color:#eef2fa;background:transparent;}'
                          'QPlainTextEdit,QListWidget,QLineEdit,QComboBox{color:#eef2fa;background:rgba(9,13,20,190);border:1px solid #303946;border-radius:10px;padding:8px;}'
                          'QTabWidget::pane{border:0;}QTabBar::tab{color:#a7b4c9;background:rgba(255,255,255,12);padding:7px 15px;margin-right:4px;border-radius:9px;}'
                          'QTabBar::tab:selected{color:#fff;background:rgba(255,255,255,30);}'+glass_controls_qss())
        layout=QVBoxLayout(self);surface=GlassFrame(radius=22);layout.addWidget(surface);body=QVBoxLayout(surface);body.setContentsMargins(22,20,22,20)
        title=QLabel(self.t('Запросы и баги','Requests and bugs'));title.setStyleSheet('font-size:24px;font-weight:800;');body.addWidget(title)
        self.tabs=QTabWidget();body.addWidget(self.tabs)
        compose=QWidget();form=QVBoxLayout(compose);form.setContentsMargins(4,16,4,4);form.setSpacing(10)
        info=QLabel(self.t('Обращение получит только выбранный администратор или модератор. Опиши проблему или запрос; логи и файлы автоматически не прикладываются.',
                          'Only the selected administrator or moderator receives your request. Describe the issue or application; logs and files are not attached automatically.'))
        info.setWordWrap(True);form.addWidget(info)
        rows=QFormLayout();self.recipient=QComboBox();rows.addRow(self.t('Кому','Recipient'),self.recipient)
        self.category=QComboBox()
        for key,ru,en in [('bug','Ошибка','Bug report'),('radio','Разрешение на радио','Radio hosting approval'),('appeal','Апелляция','Appeal'),('other','Другой запрос','Other request')]:
            self.category.addItem(self.t(ru,en),key)
        rows.addRow(self.t('Тип запроса','Request type'),self.category)
        self.category.currentIndexChanged.connect(self.category_changed)
        self.subject=QLineEdit();self.subject.setMaxLength(120);self.subject.setPlaceholderText(self.t('Коротко опиши тему обращения','Briefly describe your request'));rows.addRow(self.t('Тема','Subject'),self.subject);form.addLayout(rows)
        form.addWidget(QLabel(self.t('Опиши проблему, заявку или апелляцию','Describe the issue, application or appeal')))
        self.details=QPlainTextEdit();self.details.setPlaceholderText(self.t('Не меньше 15 символов, до 3000.','15–3000 characters.'));form.addWidget(self.details,2)
        form.addWidget(QLabel(self.t('Как повторить ошибку (необязательно)','Steps to reproduce (optional)')))
        self.steps=QPlainTextEdit();self.steps.setMaximumHeight(115);form.addWidget(self.steps,1)
        self.status=QLabel();self.status.setWordWrap(True);form.addWidget(self.status)
        footer=QHBoxLayout();self.send=QPushButton(self.t('Отправить репорт','Send report'));self.send.setObjectName('AccentBtn');self.send.clicked.connect(self.submit)
        footer.addStretch();footer.addWidget(self.send);form.addLayout(footer);self.tabs.addTab(compose,self.t('Новое обращение','New request'))
        self.sent=self.history_tab('out',self.t('Мои обращения','My requests'));self.inbox=None
        if self.node.my_role():self.inbox=self.history_tab('in',self.t('Входящие','Inbox'))
        if not embedded:
            close=QPushButton(self.t('Закрыть','Close'));close.clicked.connect(self.close);body.addWidget(close,0,Qt.AlignmentFlag.AlignRight)
        page.bridge.ev.connect(self.event_received);self.refresh()

    def t(self,ru,en):return tr(self.win,ru,en)

    def category_changed(self):
        radio=self.category.currentData()=='radio'
        if radio and not self.subject.text():self.subject.setText(self.t('Заявка на ведение радиостанции','Radio hosting application'))
        self.send.setText(self.t('Отправить заявку','Send request') if self.category.currentData()!='bug' else self.t('Отправить репорт','Send report'))

    def event_received(self,kind,data):
        if kind in ('bug_reports','staff') and self.isVisible():self.refresh()

    def history_tab(self,direction,title):
        widget=QWidget();layout=QVBoxLayout(widget);split=QSplitter();listing=QListWidget();detail=QPlainTextEdit();detail.setReadOnly(True)
        category=QComboBox()
        for key,ru,en in [('all','Все обращения','All requests'),('bug','Баги','Bugs'),('radio','Разрешения на радио','Radio approvals'),('appeal','Апелляции','Appeals'),('other','Другие запросы','Other requests')]:category.addItem(self.t(ru,en),key)
        layout.addWidget(category);listing.setProperty('support_category','all')
        category.currentIndexChanged.connect(lambda:(listing.setProperty('support_category',category.currentData()),self.refresh()))
        split.addWidget(listing);split.addWidget(detail);split.setSizes([270,500]);layout.addWidget(split)
        if direction=='in':
            done=QPushButton(self.t('Отметить просмотренным','Mark reviewed'));layout.addWidget(done)
            def mark():
                item=listing.currentItem()
                if item and self.node.my_role():
                    self.node.store.x("UPDATE bug_reports SET state='reviewed' WHERE id=? AND direction='in' AND state='new'",(item.data(Qt.ItemDataRole.UserRole)['id'],));self.refresh()
            done.clicked.connect(mark)
            reply=QPlainTextEdit();reply.setMaximumHeight(80);reply.setPlaceholderText(self.t('Ответ пользователю','Reply to user'));layout.addWidget(reply)
            buttons=QHBoxLayout()
            def respond(state):
                item=listing.currentItem()
                if self.node.loop is None:
                    self.page.toast(self.t('Включите Echoes.NET, чтобы ответить на обращение.','Enable Echoes.NET to reply to a request.'));return
                if item:self.node.call(net_bugs.answer,self.node,item.data(Qt.ItemDataRole.UserRole)['id'],reply.toPlainText(),state)
            for ru,en,state in [('Ответить','Reply','replied'),('Одобрить','Approve','approved'),('Отклонить','Decline','declined')]:
                button=QPushButton(self.t(ru,en));button.clicked.connect(lambda _=False,s=state:respond(s));buttons.addWidget(button)
            layout.addLayout(buttons)
        else:
            cancel=QPushButton(self.t('Отменить ожидание доставки','Cancel delivery retries'));layout.addWidget(cancel)
            def cancel_pending():
                item=listing.currentItem()
                if item:
                    self.node.store.x("UPDATE bug_reports SET state='cancelled' WHERE id=? AND direction='out' AND state='queued'",(item.data(Qt.ItemDataRole.UserRole)['id'],));self.refresh()
            cancel.clicked.connect(cancel_pending)
        def selected():
            item=listing.currentItem()
            if not item:detail.clear();return
            row=item.data(Qt.ItemDataRole.UserRole);p=json.loads(row['payload'])
            detail.setPlainText(p['title']+'\n\n'+self.t('Версия: ','Version: ')+p['version']+'\n'+
                                self.t('Код: ','Code: ')+'echoes:'+row['peer']+'\n\n'+p['details']+
                                ('\n\n'+self.t('Шаги:\n','Steps:\n')+p['steps'] if p['steps'] else '')+
                                ('\n\n'+self.t('Ответ:\n','Reply:\n')+p['answer'].get('text','') if p.get('answer') else '')+
                                ('\n\n'+self.t('Система:\n','System:\n')+p['diag'] if p.get('diag') else ''))
        listing.currentItemChanged.connect(selected);self.tabs.addTab(widget,title)
        return (direction,listing,detail)

    def refresh(self):
        if self.node.my_role() and self.inbox is None:self.inbox=self.history_tab('in',self.t('Входящие','Inbox'))
        if self.inbox is not None:self.tabs.setTabEnabled(2,bool(self.node.my_role()))
        previous=self.recipient.currentData();self.recipient.blockSignals(True);self.recipient.clear()
        for peer in net_bugs.recipients(self.node):
            contact=self.node.store.contact(peer) or {};role=self.t('Администратор','Administrator') if self.node.role(peer)=='admin' else self.t('Модератор','Moderator')
            self.recipient.addItem((contact.get('name') or peer[:12])+' · '+role,peer)
        self.recipient.setCurrentIndex(max(0,self.recipient.findData(previous)));self.recipient.blockSignals(False)
        self.send.setEnabled(bool(self.recipient.count()))
        if not self.recipient.count():self.status.setText(self.t('Нет доступных получателей с подтверждённой ролью.','No recipients with a verified staff role are available.'))
        states={'queued':self.t('В очереди','Queued'),'received':self.t('Получено','Received'),'new':self.t('Новое','New'),'reviewed':self.t('Просмотрено','Reviewed'),'cancelled':self.t('Ожидание отменено','Retries cancelled'),
                'approved':self.t('Одобрено','Approved'),'declined':self.t('Отклонено','Declined'),'replied':self.t('Ответ получен','Reply received'),
                'answered':self.t('Ответ доставлен','Reply delivered'),'answer_queued':self.t('Ответ в очереди','Reply queued')}
        for group in (self.sent,self.inbox):
            if group is None:continue
            direction,listing,detail=group
            if direction=='in' and not self.node.my_role():listing.clear();detail.clear();continue
            selected=listing.currentItem();ident=selected.data(Qt.ItemDataRole.UserRole)['id'] if selected else None
            listing.blockSignals(True);listing.clear()
            for row in self.node.store.q('SELECT * FROM bug_reports WHERE direction=? ORDER BY ts DESC LIMIT 150',(direction,)):
                p=json.loads(row['payload'])
                if listing.property('support_category') not in ('all',p.get('category','bug')):continue
                category={'bug':self.t('Баг','Bug'),'radio':self.t('Радио','Radio'),'appeal':self.t('Апелляция','Appeal'),'other':self.t('Запрос','Request')}.get(p.get('category','bug'),'')
                listing.addItem(category+' · '+p['title']+'\n'+states.get(row['state'],row['state'])+' · '+time.strftime('%d.%m %H:%M',time.localtime(row['ts'])))
                item=listing.item(listing.count()-1);item.setData(Qt.ItemDataRole.UserRole,row)
                if row['id']==ident:listing.setCurrentItem(item)
            listing.blockSignals(False)
            if listing.count() and listing.currentRow()<0:listing.setCurrentRow(0)
            elif listing.currentItem() is not None:listing.currentItemChanged.emit(listing.currentItem(),None)

    def submit(self):
        try:
            version=Path(__file__).with_name('version.txt').read_text('utf-8').strip()
            try:                                              # видеокарта, звук, FPS, ошибки — сразу видно, что у человека
                import diagnostics
                diag=diagnostics.report(self.win)
            except Exception:                                 # noqa: BLE001
                diag=''
            net_bugs.queue(self.node,self.recipient.currentData(),self.subject.text(),self.details.toPlainText(),self.steps.toPlainText(),version,self.category.currentData(),diag)
        except ValueError:
            self.status.setText(self.t('Проверь тему (4–120 символов), описание (15–3000), шаги (до 1500) и получателя. Очередь: до 30 репортов.',
                                      'Check subject (4–120), description (15–3000), steps (up to 1500), and recipient. The queue holds 30 reports.'))
            return
        except OSError:
            self.status.setText(self.t('Не удалось сохранить репорт. Попробуй ещё раз.','Could not save the report. Please try again.'));return
        self.subject.clear();self.details.clear();self.steps.clear();self.refresh()
        self.status.setText(self.t('Обращение сохранено в очереди. Включи Echoes.NET; ответ появится в «Моих обращениях». Для одобрения радио получателю нужна актуальная версия ECHOES.',
                                  'Request queued. Enable Echoes.NET; replies appear in My requests. Radio approval requires an updated ECHOES on the recipient’s computer.'))


def open_reports(page,inbox=False):
    dialog=getattr(page,'_bug_reports_dialog',None)
    if dialog is None:dialog=page._bug_reports_dialog=BugReportsDialog(page)
    dialog.refresh()
    if inbox and dialog.inbox is not None:dialog.tabs.setCurrentIndex(2)
    else:dialog.tabs.setCurrentIndex(0)
    dialog.show();dialog.raise_();dialog.activateWindow()
    return dialog
