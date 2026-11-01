# dsl_docs.py
"""Встроенная справка по EchoScript (HTML для окна «Справка» в студии скриптов)."""

CSS = """
<style>
 body { font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #e8e6f0; }
 h1 { font-size: 22px; color: #ffb347; margin: 4px 0 6px 0; }
 h2 { font-size: 16px; color: #6ae3ff; margin: 18px 0 6px 0; border-bottom: 1px solid #333; }
 h3 { font-size: 13px; color: #ff8fb1; margin: 12px 0 4px 0; }
 code { font-family: Consolas, 'Cascadia Mono', monospace; color: #ffd28a; }
 pre { font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px; background: #15131f;
       color: #e9e3ff; padding: 8px; border-radius: 6px; }
 td { padding: 3px 10px 3px 0; vertical-align: top; }
 .k { color: #ffd28a; font-family: Consolas, monospace; white-space: nowrap; }
 .m { color: #9c96b3; }
</style>
"""

GAME_SRC = r"""widget NightShift {
  prop night_len = 120              // длина ночи в секундах
  state t = 0
  state hour = 0
  state power = 100
  state door_l = false
  state door_r = false
  state cam = false
  state cam_room = "A"
  state enemy = "A"                 // где противник: A → B → C/L → R
  state scare = 0
  state over = ""                   // "", "win", "lose"
  state nights = load_data("nights", 0)
  state rooms = {A: "Сцена", B: "Зал", C: "Коридор", L: "Левая дверь", R: "Правая дверь"}

  on init {
    capture_keys(true, ["a", "d", "space"])
    every(4, fn() { think() })      // противник «думает» раз в 4 секунды
  }
  fn think() {
    if over != "" or not chance(0.55) { return }
    match enemy {
      "A" => { enemy = "B" }
      "B" => { enemy = choice(["C", "L"]) }
      "C" => { enemy = "R" }
      "L" => { if door_l { enemy = "B" } else { jumpscare() } }
      "R" => { if door_r { enemy = "C" } else { jumpscare() } }
    }
    if cam { beep(160, 0.25, 0.25, "saw") }
  }
  fn jumpscare() {
    over = "lose"
    scare = 1
    beep(70, 1.2, 0.9, "noise")
    tween(self, "scare", 0, 1.5, "out_quad")
  }
  fn restart() {
    t = 0
    power = 100
    door_l = false
    door_r = false
    cam = false
    enemy = "A"
    over = ""
  }
  on key(k) {
    if over != "" { return true }
    match k {
      "a" => { door_l = not door_l; beep(320, 0.06, 0.3, "square") }
      "d" => { door_r = not door_r; beep(320, 0.06, 0.3, "square") }
      "space" => { cam = not cam }
    }
    return true
  }
  on click(x, y) {
    if over != "" {
      restart()
    } elif cam and y > h - 50 {
      cam_room = ["A", "B", "C", "L", "R"][clamp(floor(x / (w / 5)), 0, 4)]
    }
  }
  on frame(dt) {
    if over != "" { return }
    t += dt
    hour = floor(t / night_len * 6)
    let use = 1 + (door_l ? 1 : 0) + (door_r ? 1 : 0) + (cam ? 1 : 0)
    power = max(0, power - use * dt * 0.35)
    if power == 0 {
      door_l = false
      door_r = false
      cam = false
      if chance(dt * 0.3) { jumpscare() }
    }
    if hour >= 6 {
      over = "win"
      nights += 1
      save_data("nights", nights)
      tone([[note("C5"), 0.15], [note("E5"), 0.15], [note("G5"), 0.4]])
    }
  }
  on draw {
    nostroke()
    fill("#07080a")
    rect(0, 0, w, h)
    if cam {
      fill("#13201a")
      rect(0, 0, w, h)
      if enemy == cam_room {
        fill("#b02828")
        circle(w / 2, h / 2, min(w, h) * 0.12)
      }
      static_noise(0, 0, w, h, enemy == cam_room ? 0.45 : 0.2)
      scanlines(0, 0, w, h, 0.25)
      fill("#9fe8b0")
      text(`CAM {cam_room} · {rooms[cam_room]}`, 20, 26, 16, weight: 700)
      for i, r in ["A", "B", "C", "L", "R"] {
        fill(r == cam_room ? "#9fe8b0" : "#ffffff22")
        rect(i * w / 5 + 6, h - 44, w / 5 - 12, 34, 6)
        fill(r == cam_room ? "#000000" : "#d0f0dc")
        text(r, i * w / 5 + w / 10, h - 27, 14, align: "center", weight: 700)
      }
    } else {
      fill(door_l ? "#5a5f66" : "#1b1d22")
      rect(0, h * 0.15, w * 0.18, h * 0.7)
      fill(door_r ? "#5a5f66" : "#1b1d22")
      rect(w * 0.82, h * 0.15, w * 0.18, h * 0.7)
      if enemy == "L" and not door_l {
        fill("#b02828")
        circle(w * 0.09, h * 0.4, 18)
      }
      if enemy == "R" and not door_r {
        fill("#b02828")
        circle(w * 0.91, h * 0.4, 18)
      }
      fill("#d8d8e0")
      text("A — левая дверь   D — правая   Пробел — камеры", w / 2, h - 22, 13, align: "center")
      vignette(0, 0, w, h, 0.8)
    }
    fill("#ffffff")
    text(`{hour == 0 ? 12 : hour} AM`, w - 20, 26, 20, align: "right", weight: 800)
    fill(power > 20 ? "#e6e6ee" : "#ff5050")
    text(`Энергия: {floor(power)}%   Ночей пережито: {nights}`, 20, h - 60, 13)
    if scare > 0.01 {
      fill(with_alpha("#ff0000", scare * 0.6))
      rect(0, 0, w, h)
    }
    if over != "" {
      fill("#000000cc")
      rect(0, 0, w, h)
      fill("#ffffff")
      text(over == "win" ? "6 AM — смена окончена" : "Вас поймали", w / 2, h / 2, 30, align: "center", weight: 800)
      text("щелчок — заново", w / 2, h / 2 + 36, 13, align: "center")
    }
  }
}"""


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


GAME_EXAMPLE = _esc(GAME_SRC)

SECTIONS = [
("Что это", """
<h1>EchoScript — язык тем ECHOES</h1>
<p>EchoScript позволяет написать <b>свою тему целиком</b>: любые виджеты (кнопки, прогресс, обложку,
списки), их поведение (мышь, анимации, физика) и визуализаторы, реагирующие на звук. Скрипт
пересобирается <b>на лету</b>: правите код — через полсекунды изменения уже на экране, а состояние
виджетов (позиции, накопленные частицы) сохраняется.</p>
<p>Файл скрипта (<code>.echo</code>) состоит из трёх частей:</p>
<pre>theme { name: "Моя тема", bg: "#0b0a14", accent: "#ffb347" }   // оформление

widget Pulse {                    // свой виджет
  on draw {
    fill(theme.accent)
    circle(w / 2, h / 2, 20 + audio.bass * 60)
  }
}

scene {                           // что и где на экране
  add Pulse { x: 0.4, y: 0.3, w: 0.2, h: 0.3 }
}</pre>
<h2>Что умеет язык</h2>
<ul>
<li><b>Виджеты внутри виджетов</b>: <code>child lib = Library { … }</code> — iPod с настоящей медиатекой внутри,
окно с кнопками, игровой экран со своими объектами. Вложенные виджеты получают клики, колесо и кадры сами.</li>
<li><b>Связь между виджетами и слоями</b>: вызывать методы другого виджета (<code>lib.move_sel(1)</code>),
сообщения <code>emit</code> / <code>send</code> / <code>post</code>, общие переменные <code>shared</code>,
поиск <code>find_widget</code>, управление любым слоем темы <code>layer("Логотип").rot = 30</code>.</li>
<li><b>Игры и интерактив</b>: клавиатура (<code>key_down("w")</code>, <code>capture_keys</code>), таймеры
<code>after</code> / <code>every</code>, плавные анимации <code>tween</code>, звуки из файлов и синтез
<code>beep</code> / <code>tone</code>, сохранения <code>save_data</code>, диалоги ввода, столкновения,
случайности, помехи камер, спрайты, холсты для рисования.</li>
<li><b>Формы любой сложности</b>: контуры (<code>shape("heart", …)</code>, SVG, свои точки), их сложение и
вычитание, маски — картинка или обложка в любой форме, клики только по форме.</li>
<li><b>Вся медиатека</b>: <code>library()</code>, плейлисты, поиск, исполнители, альбомы, очередь, темы плеера.</li>
<li><b>Удобный язык</b>: <code>match</code>, <code>try / catch</code>, строки с подстановкой
<code>`Счёт: {score}`</code>, <code>x in список</code>, методы значений <code>список.map(…)</code>,
<code>строка.upper()</code>.</li>
</ul>
"""),

("Без кода", """
<h2>Собрать тему мышью</h2>
<p>На сцене нажмите <b>«Изменить тему»</b> — откроется конструктор, и виджеты можно сразу двигать. <b>«Готово»</b> закрывает его (всё сохраняется само).</p>
<ol>
<li><b>Файл ▾ → Новый</b>: «Пустой холст», «Готовый набор» (обложка, название, прогресс, кнопки,
громкость, спектр, список треков) или «Из темы плеера» — цвета, шрифт и фон-картинка/видео
из любой темы, в том числе из конструктора тем.</li>
<li>Вкладка <b>«Конструктор»</b>: перетащите блок из списка на сцену (или щёлкните по нему). У каждого блока — живое превью. Есть фон со свечением, картинка/GIF,
видео, фигуры, текст, название трека, обложка, спектр, волна, круговой спектр, искры на бит,
кнопки, прогресс, громкость, список треков.</li>
<li><b>«Двигать»</b>: щёлкните по виджету и тяните его или его углы. Края прилипают к сетке и
к другим виджетам (Alt — без прилипания), Shift на углу сохраняет пропорции, стрелки сдвигают
(с Shift — крупнее), Delete удаляет, Esc снимает выделение, Ctrl+Z / Ctrl+Y (или ↶ ↷) отменяют и повторяют.</li>
<li><b>Несколько виджетов</b>: Ctrl+клик добавляет или убирает виджет из выделения, рамка мышью по пустому
месту (или по фону) выделяет всё, что в неё попало, Ctrl+A выделяет всё. Выделенное двигается вместе.
Можно выровнять по краям или центру, распределить с равными промежутками, сделать одной ширины или высоты.
<b>«Сгруппировать»</b> задаёт виджетам общее свойство <code>group</code>: потом щелчок по любому из них
выделяет всю группу.</li>
<li>Под блоками — <b>настройки</b> выбранного виджета: цвета, файл картинки или видео (…), размер шрифта,
режим кнопки и так далее, а также слой выше/ниже, копия, удаление и переход к его коду.</li>
<li><b>События</b> (ниже свойств) задают, что делает виджет по клику, нажатию, колесу, на удар в музыке,
при смене трека, наведении курсора: пауза, следующий трек, громкость, перемотка, экспорт или свой код.
В коде это свойство вида <code>on_click: fn() { next() }</code> в строке <code>add</code>, и оно работает
вдобавок к <code>on click</code> самого виджета.</li>
</ol>
<h2>Фигуры</h2>
<p>«Фигура»: прямоугольник, круг, кольцо, звезда (<code>sides</code> лучей), многоугольник (<code>sides</code> сторон),
шестиугольник, треугольник. «Векторный контур»: любая форма в синтаксисе SVG (свойство <code>d</code>, как в
иконках и логотипах). «Живая капля» колышется и дышит от музыки. У всех есть пульсация, вращение,
градиент и контур.</p>
<h2>Импорт из других тем</h2>
<p><b>Файл ▾ → Импорт виджетов из скрипта</b>: выберите скрипт и отметьте виджеты. Они перенесутся вместе
с нужными им функциями, цветами темы и (по желанию) расстановкой. <b>Импорт слоёв и фона из темы
конструктора</b> переносит цвета, фон (картинку или видео) и слои: картинки, GIF, видео, тексты, фигуры.</p>
<p>Всё, что делается мышью, сразу записывается в код скрипта. Его можно дописать руками, и
расстановка продолжит работать. Двигать мышью можно виджеты, добавленные отдельной строкой
<code>add</code>, а созданные в цикле помечены «(в цикле)».</p>
<h2>Скрипты и темы</h2>
<p>Каждый ваш скрипт появляется в списке тем в разделе <b>«МОИ СКРИПТЫ»</b> как «◈ Название».
В <b>«Файл ▾»</b> есть копия, переименование, экспорт в файл <code>.echo</code> (можно отправить другу),
импорт, «Взять цвета из темы» и удаление. Если поправить встроенный Loom, правки сохранятся
как ваша копия, а «Сбросить к встроенной версии» вернёт оригинал. Кнопка <b>⧉</b> или пункт
<b>«Файл ▾ → Студия в отдельном окне»</b> переключает режим: панель в плеере или отдельное окно.
Выбор запоминается.</p>
"""),

("Синтаксис", """
<h2>Синтаксис</h2>
<h3>Комментарии, переменные, присваивание</h3>
<pre>// однострочный     /* многострочный */
let x = 10           // объявить переменную (точка с запятой не нужна)
x = x + 1
x += 2   x -= 1   x *= 3   x /= 2</pre>
<h3>Значения</h3>
<table>
<tr><td class=k>12  3.5  1e3</td><td>числа</td></tr>
<tr><td class=k>"текст"  'текст'</td><td>строки (<code>\\n</code>, <code>\\t</code>, <code>\\"</code>); <code>+</code> склеивает с чем угодно</td></tr>
<tr><td class=k>true false nil</td><td>логические значения и «пусто»</td></tr>
<tr><td class=k>[1, 2, 3]</td><td>список: <code>a[0]</code>, <code>a[-1]</code>, <code>a.length</code>, <code>a.first</code>, <code>a.last</code></td></tr>
<tr><td class=k>{name: "x", "k": 1}</td><td>словарь: <code>d.name</code> или <code>d["k"]</code>; нет ключа — <code>nil</code></td></tr>
</table>
<h3>Операторы (по возрастанию приоритета)</h3>
<pre>cond ? a : b        тернарный
a or b   a and b   not a
==  !=  <  <=  >  >=
a..b                диапазон чисел a, a+1, …, b-1 (для циклов)
+  -   *  /  %   **  (деление на 0 даёт 0, а не ошибку)</pre>
<h3>Условия и циклы</h3>
<pre>if level > 0.8 { … } elif level > 0.4 { … } else { … }

for i in 0..64 { … }            // по числам
for item in list { … }          // по элементам
for i, item in list { … }       // с номером
for key, value in dict { … }    // по словарю
while x < 10 { x += 1 }
break   continue</pre>
<h3>Функции</h3>
<pre>fn smoothstep(e0, e1, x = 0.5) {
  let t = clamp((x - e0) / (e1 - e0))
  return t * t * (3 - 2 * t)
}
smoothstep(0, 1, 0.3)
smoothstep(0, 1, x: 0.3)        // именованный аргумент
let twice = fn(v) { return v * 2 }   // анонимная функция (замыкание)</pre>
<p class=m>Переменные видны внутри функции; объявленные на верхнем уровне файла — во всём скрипте.
Внутри виджета имена ищутся так: локальные → поля виджета → глобальные.</p>
"""),

("Виджеты", """
<h2>Виджеты</h2>
<pre>widget Name {
  prop size = 40          // свойство: значение по умолчанию, можно задать в add
  state angle = 0         // состояние: живёт между кадрами (при горячей перезагрузке сохраняется)
  fn helper(a) { … }      // метод (вызывается как helper(1) или self.helper(1))
  on frame(dt) { angle += dt * 90 }
  on draw { … }           // рисование, координаты — внутри виджета: (0,0) — левый верхний угол
}</pre>
<h3>Встроенные поля каждого виджета</h3>
<table>
<tr><td class=k>x, y, w, h</td><td>положение и размер в пикселях (пересчитываются из долей экрана)</td></tr>
<tr><td class=k>z</td><td>слой: больше — выше (рисуется позже и первым получает мышь)</td></tr>
<tr><td class=k>visible</td><td>показывать ли виджет</td></tr>
<tr><td class=k>clip</td><td>обрезать рисование по рамке виджета (по умолчанию true; для свечения за краями — false)</td></tr>
<tr><td class=k>hover, pressed</td><td>курсор над виджетом / зажата кнопка мыши</td></tr>
<tr><td class=k>cache, cache_key</td><td>cache: true — виджет рисуется один раз и дальше копируется картинкой;
перерисуется, когда изменится cache_key (например <code>cache_key = track.title</code>)</td></tr>
<tr><td class=k>resolution</td><td>0.1…1 — рисовать в уменьшенную картинку и растягивать (дёшево для светящихся визуализаторов)</td></tr>
<tr><td class=k>name</td><td>имя экземпляра</td></tr>
</table>
<h3>Сцена и тема</h3>
<pre>theme { name: "Neon", bg: "#05040a", accent: "#ff3d81", accent2: "#3dd6ff",
        text: "#f4f4f8", muted: "#8a8aa0", font: "Segoe UI" }
// любые свои ключи тоже можно: theme.glow, theme.radius …

scene {
  add Pulse { x: 0.1, y: 0.1, w: 0.3, h: 0.3, z: 2, size: 60 }
  for i in 0..5 {                       // в сцене работает обычный код
    add Pulse { x: 0.1 + i * 0.15, y: 0.6, w: 0.12, h: 0.2 }
  }
}</pre>
<p>Координаты: число <b>от 0 до 1</b> — доля ширины/высоты окна; <b>больше 1</b> — пиксели;
отрицательные <code>x</code>/<code>y</code> отсчитываются от правого/нижнего края.</p>
"""),

("События", """
<h2>События (on …)</h2>
<table>
<tr><td class=k>init</td><td>один раз при запуске сцены</td></tr>
<tr><td class=k>frame(dt)</td><td>каждый кадр (~60 раз в секунду); dt — секунды с прошлого кадра</td></tr>
<tr><td class=k>draw</td><td>рисование</td></tr>
<tr><td class=k>press(x, y, button)</td><td>нажатие мыши (button: 1 — левая, 2 — правая)</td></tr>
<tr><td class=k>release(x, y, button)</td><td>отпускание</td></tr>
<tr><td class=k>click(x, y, button)</td><td>клик (нажали и отпустили почти на месте)</td></tr>
<tr><td class=k>drag(x, y, dx, dy)</td><td>перетаскивание после press (dx, dy — смещение)</td></tr>
<tr><td class=k>move(x, y)</td><td>движение курсора над виджетом</td></tr>
<tr><td class=k>enter / leave</td><td>курсор зашёл / ушёл</td></tr>
<tr><td class=k>wheel(delta, x, y)</td><td>колесо мыши (delta: +1 вверх, -1 вниз)</td></tr>
<tr><td class=k>beat(power)</td><td>удар в музыке (power 0…1)</td></tr>
<tr><td class=k>track(t)</td><td>сменился трек (t — то же, что track)</td></tr>
<tr><td class=k>key(name)</td><td>нажата клавиша ("a"…"z", "0"…"9", "space", "left", "right", "up", "down",
"enter", "escape", "tab", "shift", "ctrl", "alt", "f1"…"f12"). Буквы — по физической клавише, WASD работает и в
русской раскладке. <code>return true</code> — клавиша поглощена (плеер её не получит)</td></tr>
<tr><td class=k>keyup(name)</td><td>клавиша отпущена</td></tr>
<tr><td class=k>dblclick(x, y, button)</td><td>двойной щелчок</td></tr>
<tr><td class=k>message(name, data, from)</td><td>сообщение от другого виджета (emit / send / post);
<code>return</code> — ответ для send</td></tr>
<tr><td class=k>resize(w, h)</td><td>изменился размер виджета</td></tr>
<tr><td class=k>destroy</td><td>виджет удаляют (kill)</td></tr>
</table>
<p class=m>Параметры можно не объявлять: <code>on click { … }</code> тоже работает.
Координаты мыши — внутри виджета (у вложенного — внутри него самого).</p>
"""),

("Звук и плеер", """
<h2>Звук: audio</h2>
<table>
<tr><td class=k>audio.fft</td><td>спектр: список из 64 полос 0…1 (от низких к высоким, логарифмически)</td></tr>
<tr><td class=k>audio.bass / mid / high</td><td>энергия низов / середины / верхов 0…1 (с автоусилением)</td></tr>
<tr><td class=k>audio.level</td><td>общая громкость 0…1</td></tr>
<tr><td class=k>audio.peak</td><td>пик с плавным спадом</td></tr>
<tr><td class=k>audio.beat, audio.beat_power</td><td>в этом кадре удар? и его сила</td></tr>
<tr><td class=k>audio.wave</td><td>форма волны: 128 значений -1…1</td></tr>
</table>
<h2>Трек: track</h2>
<p><code>track.title, artist, album, cover</code> (путь к картинке), <code>duration, position</code> (секунды),
<code>progress</code> (0…1), <code>loaded</code> (true, если трек выбран).</p>
<h2>Плеер: player, playlist</h2>
<p><code>player.playing, volume</code> (0…1), <code>shuffle, repeat</code> (0 — нет, 1 — все, 2 — один)<br>
<code>playlist.name, tracks</code> (список {title, artist, duration, cover}), <code>index</code> (играющий), <code>count</code></p>
<h2>Время и экран</h2>
<p><code>time</code> (секунды с запуска), <code>dt</code>, <code>frame</code>, <code>W, H</code> (размер всей сцены),
<code>mouse.x, mouse.y, mouse.down</code> (в координатах сцены), <code>theme</code> — словарь темы.</p>
<h2>Действия</h2>
<table>
<tr><td class=k>toggle() play() pause()</td><td>воспроизведение</td></tr>
<tr><td class=k>next() prev()</td><td>следующий / предыдущий трек</td></tr>
<tr><td class=k>seek(frac)</td><td>перемотать к доле трека 0…1</td></tr>
<tr><td class=k>set_volume(v)</td><td>громкость 0…1</td></tr>
<tr><td class=k>play_index(i)</td><td>включить i-й трек текущего плейлиста</td></tr>
<tr><td class=k>toggle_shuffle() cycle_repeat()</td><td>перемешивание / режим повтора</td></tr>
<tr><td class=k>track_menu(path)   reveal(path)</td><td>меню трека (играть, показать в проводнике, теги) / открыть папку с файлом трека</td></tr>
<tr><td class=k>artist_shuffle(имя?) artist_menu()</td><td>шаффл по исполнителю: все его треки вперемешку (без имени — исполнитель текущего трека) / меню выбора исполнителя</td></tr>
<tr><td class=k>export_fx()</td><td>открыть «Экспорт с эффектами» для играющего трека</td></tr>
</table>
"""),

("Рисование", """
<h2>Рисование (только в on draw)</h2>
<h3>Кисть и перо</h3>
<table>
<tr><td class=k>fill(color)  nofill()</td><td>заливка фигур (цвет или градиент)</td></tr>
<tr><td class=k>stroke(color, width = 1, cap = "round")</td><td>контур; cap: "round" | "flat" | "square"</td></tr>
<tr><td class=k>nostroke()</td><td>без контура</td></tr>
<tr><td class=k>alpha(a)</td><td>прозрачность всего, что рисуется дальше (медленнее, чем цвет с прозрачностью)</td></tr>
<tr><td class=k>blend(mode)</td><td>"normal", "add" (свечение), "screen", "multiply", "overlay", "lighten", "darken", "difference"</td></tr>
</table>
<h3>Фигуры</h3>
<table>
<tr><td class=k>rect(x, y, w, h, r = 0)</td><td>прямоугольник (r — скругление)</td></tr>
<tr><td class=k>circle(x, y, r)   ellipse(x, y, rx, ry)</td><td>круг / эллипс</td></tr>
<tr><td class=k>line(x1, y1, x2, y2)</td><td>отрезок</td></tr>
<tr><td class=k>lines([x1, y1, x2, y2, …])</td><td>много отрезков за один вызов (быстро)</td></tr>
<tr><td class=k>poly(points, closed = true)</td><td>многоугольник / ломаная; points — [x1, y1, x2, y2 …] или [[x, y], …]</td></tr>
<tr><td class=k>curve(points, closed = false, tension = 0.5)</td><td>плавная кривая через точки</td></tr>
<tr><td class=k>arc(x, y, r, a0, a1)</td><td>дуга, углы в градусах (0 — вправо, по часовой)</td></tr>
<tr><td class=k>ring(x, y, r1, r2)</td><td>кольцо</td></tr>
<tr><td class=k>star(x, y, r1, r2, n = 5, rot = 0)</td><td>звезда</td></tr>
<tr><td class=k>ngon(x, y, r, n = 6, rot = 0)</td><td>правильный многоугольник</td></tr>
<tr><td class=k>glow(x, y, r, color, strength = 1)</td><td>мягкое свечение (лучше с blend("add"))</td></tr>
<tr><td class=k>clear(color)</td><td>залить всё</td></tr>
</table>
<h3>Текст и картинки</h3>
<table>
<tr><td class=k>text(s, x, y, size = 14, align = "left", weight = 500, font = nil, maxw = 0)</td>
<td>y — середина строки; align: "left" | "center" | "right"; maxw — обрезать «…»</td></tr>
<tr><td class=k>textwidth(s, size = 14, weight = 500)</td><td>ширина текста</td></tr>
<tr><td class=k>image(path, x, y, w, h, r = 0)</td><td>картинка (обрезка по центру без искажений); возвращает false, если её нет.
GIF/WEBP проигрываются. Обложка трека: <code>image(track.cover, …)</code></td></tr>
<tr><td class=k>video(path, x, y, w, h, r = 0, rate = 1)</td><td>видео без звука, по кругу (MP4, WEBM, MOV…);
rate — скорость. Декодер сам останавливается, когда видео перестают рисовать</td></tr>
<tr><td class=k>path(d, x = 0, y = 0, w = 0, h = 0)</td><td>векторный контур SVG (M L H V C S Q T A Z).
Если заданы w и h, он вписывается в прямоугольник с сохранением пропорций. Пример:
<code>path("M12 2 L22 22 H2 Z", 0, 0, w, h)</code></td></tr>
</table>
<h3>Трансформации</h3>
<p><code>save()</code> / <code>restore()</code> — запомнить и вернуть состояние; <code>translate(x, y)</code>,
<code>rotate(deg)</code>, <code>scale(sx, sy)</code>, <code>clip(x, y, w, h, r = 0)</code>.</p>
<h3>Цвета и градиенты</h3>
<table>
<tr><td class=k>"#ff3d81"  "#ff3d8180"  "red"</td><td>цвет строкой (#rrggbb, #rrggbbaa, имя)</td></tr>
<tr><td class=k>rgb(r, g, b, a = 1)</td><td>0…255 и прозрачность 0…1</td></tr>
<tr><td class=k>hsv(h, s = 1, v = 1, a = 1)</td><td>оттенок в градусах 0…360</td></tr>
<tr><td class=k>mix(c1, c2, t)   with_alpha(c, a)   hex(c)</td><td>смешать / прозрачность / в строку</td></tr>
<tr><td class=k>linear(x1, y1, x2, y2, stops)</td><td>линейный градиент</td></tr>
<tr><td class=k>radial(cx, cy, r, stops)   conic(cx, cy, angle, stops)</td><td>радиальный / конический</td></tr>
</table>
<p class=m>stops — список цветов (равномерно) или пар [[0, "#000"], [0.7, "#f0f"], [1, "#fff"]].
Градиент передаётся в fill() или stroke().</p>
"""),

("Математика", """
<h2>Математика и утилиты</h2>
<table>
<tr><td class=k>PI TAU E</td><td>константы</td></tr>
<tr><td class=k>sin cos tan asin acos atan atan2 sqrt abs min max floor ceil round pow exp ln log10 sign hypot deg rad</td><td>как обычно (радианы)</td></tr>
<tr><td class=k>clamp(v, lo = 0, hi = 1)  lerp(a, b, t)  remap(v, a, b, c, d)  smooth(t)</td><td>ограничить / смешать / перевести диапазон / плавный шаг</td></tr>
<tr><td class=k>noise(x, y = 0, z = 0)</td><td>плавный шум Перлина (-1…1) — для «живых» движений</td></tr>
<tr><td class=k>rand(a = 0, b = 1)  randint(a, b)  choice(list)  seed(s)</td><td>случайные числа</td></tr>
<tr><td class=k>len str num int</td><td>длина, в строку, в число</td></tr>
<tr><td class=k>push(list, v…) pop(list, i = -1) insert remove slice range sort reverse contains keys values</td><td>списки и словари</td></tr>
<tr><td class=k>sum(list, a, b)  avg(list, a, b)  fill_list(n, v)</td><td>сумма / среднее (части списка) / список из n одинаковых</td></tr>
<tr><td class=k>join split upper lower fmt(v, digits) fmt_time(sec)</td><td>строки; fmt_time(75) → "1:15"</td></tr>
<tr><td class=k>log(…)  print(…)</td><td>вывод в журнал студии</td></tr>
</table>
<h2>Анимация и физика</h2>
<table>
<tr><td class=k>approach(cur, target, rate, dt)</td><td>плавное приближение к цели, не зависит от FPS</td></tr>
<tr><td class=k>spring(pos, vel, target, k = 120, damping = 12, dt)</td><td>пружина → [новая позиция, новая скорость]</td></tr>
</table>
<pre>widget Bouncer {
  state y = 0
  state vy = 0
  on beat(p) { vy -= 400 * p }            // удар подбрасывает
  on frame(dt) {
    let s = spring(y, vy, 0, 90, 6, dt)
    y = s[0]
    vy = s[1]
  }
  on draw { fill(theme.accent); circle(w / 2, h / 2 + y, 18) }
}</pre>
"""),

("Язык: ещё", """
<h2>Ещё возможности языка</h2>
<h3>match — выбор из вариантов</h3>
<pre>match mode {
  "menu" => draw_menu()
  "game", "pause" => { draw_game(); if mode == "pause" { draw_pause() } }
  0..10 => log("число от 0 до 9")          // диапазон
  else => log("что-то другое")
}</pre>
<h3>try / catch — поймать ошибку</h3>
<pre>try {
  let d = from_json(text)
  score = d.score
} catch err {                     // err — текст ошибки (имя можно не писать)
  log("плохие данные:", err)
}</pre>
<h3>Строки с подстановкой (обратные кавычки)</h3>
<pre>text(`Счёт: {score}  ·  Жизни: {lives}`, 10, 20)
let s = `можно
в несколько строк`</pre>
<h3>in, %=, методы значений</h3>
<pre>if key in ["w", "up"] { … }      if "x" in dict { … }      if s not in list { … }
n %= 10
[3, 1, 2].sort()   lst.push(4)   lst.map(fn(v) { return v * 2 })   lst.filter(fn(v) { return v > 1 })
lst.find(fn(v) { return v.id == 3 })   lst.contains(2)   lst.join(", ")   lst.random()   lst.sum()
"Привет".upper()   s.split(",")   s.replace("a", "b")   s.trim()   s.starts_with("x")   s.words()
d.keys()   d.values()   d.has("k")   d.get("k", 0)   d.remove("k")   d.merge(other)</pre>
<p class=m>Своё поле словаря с тем же именем важнее метода: у <code>{keys: 5}</code> <code>d.keys</code> — это 5.</p>
"""),

("Вложенные виджеты", """
<h2>Виджет внутри виджета</h2>
<p>Любой виджет может содержать другие виджеты — свои (из этого же кода) или готовые блоки каталога
(<code>Library</code>, <code>CoverShape</code>, <code>Tracklist</code>, <code>Spectrum</code>, <code>Vinyl</code>,
<code>ActionButton</code>… — по имени класса или ключу блока: <code>"library"</code>, <code>"cover_shape"</code>).
Вложенный виджет живёт сам: получает кадры, бит, смену трека, клики, колесо, наведение.</p>
<h3>child — объявить в виджете</h3>
<pre>widget Pod {
  child lib = Library { x: 0.05, y: 0.1, w: 0.9, h: 0.6, row: 30 }
  child art = CoverShape { x: 0.3, y: 0.72, w: 0.4, h: 0.26, shape: "heart" }
  on draw { fill("#222"); rect(0, 0, w, h, 20) }      // вложенные рисуются поверх этого
}</pre>
<p>Координаты вложенного — относительно родителя: <b>до 1</b> — доля размера родителя, <b>больше 1</b> — пиксели,
отрицательные — от правого/нижнего края. При изменении размера родителя пересчитываются сами.</p>
<h3>spawn / kill — создать и удалить кодом</h3>
<pre>on click(x, y) {
  let b = spawn("CoverShape", {x: x - 40, y: y - 40, w: 80, h: 80, shape: "star"})
  after(2, fn() { kill(b) })                 // убрать через 2 секунды
}</pre>
<p><code>spawn</code> в коде сцены (не в виджете) добавляет виджет верхнего уровня.</p>
<h3>draw_child — нарисовать там, где нужно</h3>
<pre>on draw {
  rect(0, 0, w, h)
  save(); clip(10, 30, w - 20, h - 40, 8)
  draw_child(lib, 10, 30, w - 20, h - 40)   // здесь и такого размера; клики тоже попадут сюда
  restore()
  text("поверх списка", 20, 20)
}</pre>
<p>После первого <code>draw_child</code> виджет больше не рисуется сам (<code>auto = false</code>).
<code>visible = false</code> прячет его и отключает его клики.</p>
<h3>Управление вложенным</h3>
<table>
<tr><td class=k>lib.scroll = 0   lib.move_sel(1)</td><td>поля и методы вложенного — напрямую</td></tr>
<tr><td class=k>place(c, x, y, w, h)</td><td>переставить (доли родителя или пиксели)</td></tr>
<tr><td class=k>children()   c.parent   parent_of(c)</td><td>список вложенных / родитель</td></tr>
<tr><td class=k>bring_to_front(c)   send_to_back(c)</td><td>выше / ниже соседей (поле z)</td></tr>
<tr><td class=k>c.rot   c.scale   c.opacity</td><td>поворот (°), масштаб и прозрачность вложенного (клики учитывают поворот)</td></tr>
<tr><td class=k>c.visible   c.clip   c.z</td><td>как у любого виджета</td></tr>
</table>
<p>Целый пример — блок <b>«Плеер в стиле iPod»</b>: внутри него медиатека и обложка, колесо крутится мышью.</p>
"""),

("Связь виджетов", """
<h2>Виджеты и слои разговаривают</h2>
<h3>Сообщения</h3>
<table>
<tr><td class=k>emit(name, data)</td><td>сообщение родителю</td></tr>
<tr><td class=k>send(виджет_или_имя, name, data)</td><td>сообщение конкретному виджету → его ответ (return в on message)</td></tr>
<tr><td class=k>post(name, data)</td><td>всем виджетам всех слоёв темы (у кого есть on message) → сколько получили</td></tr>
</table>
<pre>widget Item {
  on click { emit("выбран", self.name) }
}
widget Menu {
  child a = Item { name: "Играть", y: 0, h: 0.3 }
  child b = Item { name: "Выход", y: 0.4, h: 0.3 }
  on message(name, data, from) {
    if name == "выбран" { log("нажали", data) }
  }
}</pre>
<h3>Найти и вызвать</h3>
<table>
<tr><td class=k>find_widget(имя)</td><td>виджет по имени (<code>name</code> в add/spawn), классу или имени слоя — в любом слое темы</td></tr>
<tr><td class=k>find_widgets(имя)</td><td>все такие виджеты</td></tr>
<tr><td class=k>widgets()</td><td>все виджеты этого слоя</td></tr>
<tr><td class=k>widget_at(x, y)</td><td>виджет под точкой</td></tr>
</table>
<pre>let lib = find_widget("Library")
if lib != nil { lib.set_filter("rock"); lib.open(0) }</pre>
<h3>shared — общие переменные всех слоёв</h3>
<pre>shared.score = (shared.score or 0) + 1      // в одном слое
text(`Счёт: {shared.score}`, 10, 20)        // в другом</pre>
<h3>layer — управлять любым слоем темы</h3>
<pre>let l = layer("Логотип")                    // имя слоя в конструкторе
l.rot = sin(time) * 20                      // поворот, прибавляется к анимациям слоя
l.dx = 30     l.dy = -10                    // сдвиг, px
l.scale = 1.2     l.opacity = 0.5           // умножаются
l.hidden = true    l.show()    l.hide()    l.toggle()    l.reset()
l.tint = "#ff66aa"     l.label = "Новый текст"   // цвет и текст (текст и фигуры)
l.widget                                    // виджет слоя-виджета: l.widget.move_sel(1)
layers()                                    // имена всех слоёв</pre>
"""),

("Время и анимация", """
<h2>Таймеры</h2>
<table>
<tr><td class=k>after(sec, fn)</td><td>один раз через sec секунд → номер</td></tr>
<tr><td class=k>every(sec, fn)</td><td>каждые sec секунд → номер</td></tr>
<tr><td class=k>cancel(номер)</td><td>отменить таймер или tween; <code>cancel()</code> — все таймеры виджета</td></tr>
</table>
<pre>on init {
  every(1, fn() { seconds += 1 })
  after(5, fn() { mode = "game" })
}</pre>
<h2>Плавные изменения: tween</h2>
<p><code>tween(цель, "поле", значение, секунды, плавность = "out_cubic", fn_по_окончании)</code> — плавно доводит поле
виджета (<code>self</code>, вложенный) или словаря до значения. Числа, цвета, списки чисел.</p>
<pre>on click { tween(self, "k", k > 0.5 ? 0 : 1, 0.5, "out_back") }
tween(art, "rot", 360, 1.2, "in_out_sine", fn() { art.rot = 0 })
tween(colors, "bg", "#ff3d81", 0.3)</pre>
<p><code>tween_stop(цель, "поле")</code> — остановить. <code>ease(имя, t)</code> — сама кривая (t — 0…1).
Имена: <code>linear</code>, <code>smooth</code> и <code>in_</code> / <code>out_</code> / <code>in_out_</code> +
<code>quad cubic quart quint sine expo circ back elastic bounce</code>.</p>
"""),

("Клавиши и мышь", """
<h2>Клавиатура</h2>
<table>
<tr><td class=k>on key(k) / on keyup(k)</td><td>нажатие / отпускание; <code>return true</code> — забрать клавишу у плеера</td></tr>
<tr><td class=k>key_down("w")</td><td>зажата ли клавиша сейчас — для движения в играх</td></tr>
<tr><td class=k>keys_pressed()</td><td>список зажатых</td></tr>
<tr><td class=k>capture_keys()</td><td>все клавиши — только скриптам (пробел не ставит паузу, стрелки не листают);
<code>capture_keys(true, ["w", "a", "s", "d", "space"])</code> — только эти; <code>capture_keys(false)</code> — вернуть</td></tr>
</table>
<p class=m>Когда курсор в поле ввода (поиск и т.п.), клавиши скриптам не идут.</p>
<pre>state px = 100
on frame(dt) {
  if key_down("a") or key_down("left") { px -= 300 * dt }
  if key_down("d") or key_down("right") { px += 300 * dt }
}</pre>
<h2>Мышь</h2>
<table>
<tr><td class=k>mouse_in()</td><td>курсор относительно этого виджета: {x, y, inside, down}</td></tr>
<tr><td class=k>mouse.x, mouse.y</td><td>курсор в координатах окна</td></tr>
<tr><td class=k>self.hit_shape = контур</td><td>клики только по форме (круглая кнопка, дверь сложной формы)</td></tr>
<tr><td class=k>cursor("hand")</td><td>курсор над виджетом: hand, arrow, cross, none, text, move, wait, grab, grabbing, forbidden</td></tr>
</table>
"""),

("Звук", """
<h2>Звуки</h2>
<table>
<tr><td class=k>sound(path, volume = 1, pan = 0, rate = 1, loop = false)</td><td>звук из файла (wav, mp3, ogg, flac, m4a…)
поверх музыки → номер. pan: -1 слева … 1 справа; rate — скорость и высота</td></tr>
<tr><td class=k>sound_loop(path, volume)</td><td>по кругу (фон, гул вентилятора)</td></tr>
<tr><td class=k>sound_stop(id, fade = 0)   sound_stop_all()</td><td>остановить (fade — затухание, секунд)</td></tr>
<tr><td class=k>sound_volume(id, v)   sound_fade(id, to, sec)   sound_pan(id, p)   sound_rate(id, r)</td><td>менять на лету</td></tr>
<tr><td class=k>sound_pause(id, on)   sound_seek(id, sec)   sound_playing(id)   sound_pos(id)</td><td>пауза, перемотка, вопросы</td></tr>
<tr><td class=k>sound_length(path)   sound_preload(path)</td><td>длина / заранее загрузить</td></tr>
<tr><td class=k>beep(freq = 880, dur = 0.15, volume = 0.5, wave = "sine", sweep = 0, pan = 0)</td><td>синтезированный звук без файла:
sine, square, saw, triangle, noise; sweep — скольжение частоты</td></tr>
<tr><td class=k>tone([[частота, длительность], …], volume, wave)</td><td>мелодия; частота 0 — пауза</td></tr>
<tr><td class=k>note("A4")</td><td>частота ноты → 440 (C#5, Eb3 …)</td></tr>
<tr><td class=k>sound_master(v)</td><td>общая громкость звуков скриптов 0…2</td></tr>
</table>
<p class=m>Файлы загружаются в фоне: первый вызов может прозвучать с небольшой задержкой — вызовите
<code>sound_preload</code> заранее (например в on init). Пути «assets/…» — файлы темы конструктора.</p>
"""),

("Формы и маски", """
<h2>Контуры любой формы</h2>
<p><code>shape(вид, x, y, w, h, настройки)</code> → контур, вписанный в прямоугольник. Виды: <code>rect square rounded
circle ellipse squircle triangle diamond pentagon hexagon octagon ngon star heart blob flower wave gear drop leaf pill
shield cross ring arch cloud moon badge parallelogram trapezoid svg points</code>.</p>
<p>Настройки (словарь): <code>sides</code> (ngon), <code>points</code> и <code>inner</code> (звезда), <code>radius</code>
(rounded), <code>petals</code> и <code>depth</code> (flower), <code>teeth</code> и <code>hole</code> (gear),
<code>waves</code> и <code>amp</code> (wave), <code>wobble</code>, <code>t</code>, <code>seed</code> (blob — передавайте
<code>t: time</code>, чтобы колыхалась), <code>d</code> (svg), <code>points</code> «x,y x,y…» или список (points),
<code>smooth</code>, <code>rot</code> (поворот, °), <code>stretch</code> (растянуть на весь прямоугольник).</p>
<table>
<tr><td class=k>draw_path(p)</td><td>нарисовать текущими fill и stroke</td></tr>
<tr><td class=k>clip_path(p)</td><td>маска: дальше рисуется только внутри (до restore())</td></tr>
<tr><td class=k>draw_shape(вид, x, y, w, h, opts)   clip_shape(…)</td><td>то же сразу по виду</td></tr>
<tr><td class=k>image_shape(path, вид, x, y, w, h, opts)</td><td>картинка в форме: <code>image_shape(track.cover, "heart", 0, 0, w, h)</code></td></tr>
<tr><td class=k>path_svg("M0 0 L10 0 …")</td><td>контур из SVG</td></tr>
<tr><td class=k>path_new()</td><td>пустой контур, строится методами</td></tr>
</table>
<h3>Методы контура</h3>
<pre>let p = path_new().move(0, 0).line(100, 0).curve(120, 40, 60, 90, 0, 60).close()
p.rect(x, y, w, h, r)  p.circle(x, y, r)  p.ellipse(x, y, rx, ry)  p.poly(points)  p.smooth(points)
p.quad(cx, cy, x, y)   p.arc(cx, cy, r, from_deg, sweep_deg)   p.text("ECHOES", x, y, size)
p.union(q)  p.minus(q)  p.intersect(q)  p.outline(width)          // новые контуры
p.moved(dx, dy)  p.rotated(deg)  p.scaled(k)  p.fit(x, y, w, h)  p.copy()
p.contains(x, y)   p.point_at(0.5) → {x, y, angle}   p.bounds() → {x, y, w, h}   p.length</pre>
<p>Готовый блок — <b>«Обложка любой формы»</b>: форма, вращение, пульс, контур, свой SVG или точки.</p>
"""),

("Холсты и картинки", """
<h2>Свои холсты</h2>
<p><code>canvas(w, h)</code> — картинка в памяти, рисунок в ней не стирается между кадрами: следы, «краска»,
пиксель-арт, мини-карта.</p>
<table>
<tr><td class=k>draw_on(c, fn() { … })</td><td>всё рисование внутри fn попадает в холст</td></tr>
<tr><td class=k>draw_canvas(c, x, y, w, h, smooth = true)</td><td>показать холст (smooth: false — чёткие пиксели)</td></tr>
<tr><td class=k>pixel(c, x, y, color)   get_pixel(c, x, y)</td><td>поставить / прочитать пиксель</td></tr>
<tr><td class=k>canvas_clear(c, color = nil)   blur_canvas(c, sigma)</td><td>очистить / размыть</td></tr>
<tr><td class=k>c.w   c.h   c.resize(w, h)   c.save(path)</td><td>размер, изменить, сохранить PNG</td></tr>
</table>
<h2>Картинки</h2>
<table>
<tr><td class=k>thumb(path, x, y, w, h, r)</td><td>маленькая картинка, грузится в фоне — для списков из сотен обложек</td></tr>
<tr><td class=k>image_ex(path, x, y, w, h, {fit, r, flip_x, flip_y, opacity, sx, sy, sw, sh, smooth})</td>
<td>fit: "cover" | "contain" | "stretch"; sx…sh — часть картинки</td></tr>
<tr><td class=k>image_part(path, sx, sy, sw, sh, x, y, w, h)</td><td>кусок картинки</td></tr>
<tr><td class=k>sprite(path, frame, cols, rows, x, y, w, h)</td><td>кадр из листа спрайтов (анимация персонажа)</td></tr>
<tr><td class=k>image_size(path)</td><td>{w, h}</td></tr>
</table>
<h2>Текст и эффекты</h2>
<table>
<tr><td class=k>text_box(s, x, y, w, h, size, align, valign, weight, font)</td><td>многострочный текст с переносом → высота</td></tr>
<tr><td class=k>text_height(s, w, size)</td><td>сколько займёт</td></tr>
<tr><td class=k>font_load(path)</td><td>свой шрифт .ttf/.otf → имя для <code>font:</code></td></tr>
<tr><td class=k>shadow(x, y, w, h, r, blur, color, dx, dy)</td><td>мягкая тень</td></tr>
<tr><td class=k>static_noise(x, y, w, h, amount, color = false)</td><td>помехи как на камерах наблюдения</td></tr>
<tr><td class=k>scanlines(x, y, w, h, alpha, gap)   vignette(x, y, w, h, strength, color)</td><td>строки ЭЛТ / затемнение краёв</td></tr>
<tr><td class=k>rect4(x, y, w, h, tl, tr, br, bl)</td><td>прямоугольник с разным скруглением углов</td></tr>
<tr><td class=k>stroke_dash([6, 4])</td><td>пунктир (после stroke)</td></tr>
<tr><td class=k>antialias(on)   smooth_images(on)</td><td>сглаживание фигур / картинок (выкл. — пиксель-арт)</td></tr>
</table>
"""),

("Медиатека", """
<h2>Медиатека и плеер</h2>
<table>
<tr><td class=k>library()</td><td>все треки: {title, artist, album, duration, cover, path, index, year, genre}</td></tr>
<tr><td class=k>search_tracks(запрос, limit = 200)</td><td>поиск по названию, исполнителю, альбому</td></tr>
<tr><td class=k>artists()   albums()</td><td>[{name, count}] по алфавиту</td></tr>
<tr><td class=k>by_artist(имя)   by_album(имя)</td><td>треки исполнителя / альбома</td></tr>
<tr><td class=k>playlists()   playlist_tracks(имя)</td><td>имена плейлистов / треки плейлиста</td></tr>
<tr><td class=k>play_track(index, context = "library")</td><td>включить трек медиатеки (или плейлиста)</td></tr>
<tr><td class=k>play_path(path)</td><td>включить по пути файла</td></tr>
<tr><td class=k>queue_add(трек_или_index, next = false)   queue()</td><td>в очередь («играть следующим» — next: true) / очередь</td></tr>
<tr><td class=k>set_shuffle(on)   set_repeat(0 | 1 | 2)   seek_to(сек)</td><td>перемешивание, повтор, перемотка в секундах</td></tr>
<tr><td class=k>themes()   set_theme(имя)</td><td>темы плеера / включить тему</td></tr>
<tr><td class=k>toast(text)</td><td>всплывающая подсказка в окне плеера</td></tr>
<tr><td class=k>lyrics_mode() show_queue() cover_mode() clip_mode() music_hub() fullscreen() mute() …</td><td>кнопки интерфейса плеера</td></tr>
</table>
<p>Готовые блоки: <b>«Медиатека»</b> (поиск, обложки; методы <code>move_sel</code>, <code>open</code>,
<code>set_filter</code>, <code>selected</code>) и <b>«Плеер в стиле iPod»</b>.</p>
"""),

("Данные и окна", """
<h2>Сохранения</h2>
<table>
<tr><td class=k>save_data(key, value)</td><td>сохранить (переживёт перезапуск плеера): числа, строки, списки, словари</td></tr>
<tr><td class=k>load_data(key, default)</td><td>прочитать</td></tr>
<tr><td class=k>delete_data(key)   data_keys()</td><td>удалить / все ключи</td></tr>
<tr><td class=k>to_json(v, pretty)   from_json(s, default)</td><td>в текст JSON и обратно</td></tr>
</table>
<h2>Окна и система</h2>
<table>
<tr><td class=k>prompt(title, label, default, fn(text) { … })</td><td>спросить текст (nil — отмена)</td></tr>
<tr><td class=k>ask(text, fn(yes) { … })</td><td>вопрос Да/Нет</td></tr>
<tr><td class=k>pick_file(title, filter, fn(path) { … })</td><td>выбрать файл, filter например "Звуки (*.wav *.mp3)"</td></tr>
<tr><td class=k>open_url(url)</td><td>открыть ссылку (http/https) или файл</td></tr>
<tr><td class=k>copy_text(s)   paste_text()</td><td>буфер обмена</td></tr>
</table>
<p class=m>У prompt/ask/pick_file функция-ответ необязательна, но с ней окно откроется после текущего клика, а не
посреди него — так надёжнее.</p>
"""),

("Все функции", """
<h2>Библиотека функций</h2>
<h3>Строки</h3>
<p><code>replace trim starts_with ends_with find_str substr repeat pad_left pad_right chars char code format title_case
capitalize count_of is_digit is_alpha wrap_words join split upper lower fmt fmt_time fmt_num percent</code></p>
<pre>format("{0} из {1}", 3, 10)    format("{name}: {score}", {name: "Игрок", score: 5})
wrap_words("длинный текст …", 20) → список строк</pre>
<h3>Списки</h3>
<p><code>push pop insert remove slice range sort reverse contains map filter reduce each find find_index index_of unique
shuffle zip flatten concat copy deep_copy count any all min_of max_of min_by max_by group_by chunk take drop sum avg
first last swap rotate_list list_of grid_of fill_list</code></p>
<pre>list_of(5, fn(i) { return i * i })   grid_of(10, 8, 0)   sort(enemies, "hp", true)
group_by(library(), "artist")        lst.max_by(fn(t) { return t.duration })</pre>
<h3>Словари и типы</h3>
<p><code>keys values items from_items has get_or del_key merge type is_num is_str is_list is_dict is_fn is_nil
is_widget default</code></p>
<h3>Математика и случайность</h3>
<p><code>wrap fract mod step pingpong round_to snap trunc log2 cbrt is_nan INF chance rand_gauss weighted_choice uid
bezier catmull fbm noise2 bit_and bit_or bit_xor bit_not shl shr</code> (и прежние sin, cos, clamp, lerp, noise…)</p>
<h3>Геометрия и столкновения</h3>
<p><code>point_in_rect point_in_circle rect_hit circle_hit circle_rect_hit line_hit dist dist2 angle_to vec_len
normalize dot cross rotate_point angle_diff lerp_angle move_toward</code></p>
<h3>Цвета</h3>
<p><code>lighten darken saturate grayscale invert hsl color_rgb brightness contrast_text gradient_at palette_of</code>
(и rgb, hsv, mix, with_alpha, hex)</p>
<h3>Время</h3>
<p><code>now() date() clock(seconds) fmt_date("%d.%m.%Y %H:%M")</code> — дата: {year, month, day, hour, minute,
second, weekday, weekday_name, month_name}</p>
"""),

("Игры", """
<h2>Как сделать игру (что угодно)</h2>
<p>Игра — это один виджет-слой на всё окно (или несколько слоёв, связанных через <code>shared</code> и
<code>post</code>). Состояние — в <code>state</code>, логика — в <code>on frame</code>, рисование — в
<code>on draw</code>, ввод — <code>on key</code> / <code>key_down</code> / <code>on click</code>, события по
времени — <code>every</code> / <code>after</code>, эффекты — <code>tween</code>, звуки — <code>sound</code> /
<code>beep</code>, рекорды — <code>save_data</code>. Ниже — каркас игры «ночная смена»: камеры с помехами,
двери, расход энергии, противник, который ходит по комнатам, испуг и победа к 6 утра.</p>
<pre>""" + GAME_EXAMPLE + """</pre>
<p class=m>Сделайте слой-виджет на всё окно (конструктор → Слои → + Виджет → Пустой скрипт, растяните на весь экран),
вставьте код и сделайте свои картинки комнат: <code>image("assets/room_a.png", 0, 0, w, h)</code>.</p>
"""),

("Скорость", """
<h2>Чтобы было плавно (60 кадров/с)</h2>
<p>Сцена рисуется <b>видеокартой</b> (OpenGL, сглаживание 4×), если она есть. Если нет, сцена рисуется
процессором. Переключатель — «Файл ▾ → Рисовать видеокартой». Скорость видна внизу студии:
кадров в секунду и время кадра.</p>
<ul>
<li>Статичное (фон, рамки, подписи) — <code>cache: true</code>; меняется редко — <code>cache_key</code>.</li>
<li>Тяжёлый светящийся визуализатор — <code>resolution: 0.5…0.7</code>. Это работает только при рисовании
процессором; на видеокарте виджет и так рисуется в полном разрешении.</li>
<li>Много отрезков — один <code>lines([...])</code> вместо <code>line()</code> в цикле.</li>
<li>Прозрачность — в цвете (<code>with_alpha(c, 0.5)</code>), а не <code>alpha()</code> на каждую фигуру.</li>
<li>Тяжёлые вычисления — в <code>on frame</code>, а в <code>on draw</code> — только рисование.</li>
<li>У каждого обработчика есть лимит шагов: бесконечный цикл не повесит плеер — виджет просто
остановится с ошибкой, а остальные продолжат работать.</li>
</ul>
<h2>Ошибки</h2>
<p>Синтаксическая ошибка — продолжает работать прежняя версия сцены, а внизу студии видно
строку и причину. Ошибка во время работы выключает только тот виджет, где она случилась.</p>
"""),

("Примеры", """
<h2>Примеры</h2>
<h3>Круговой спектр</h3>
<pre>widget Halo {
  state spin = 0
  on frame(dt) { spin += dt * (10 + audio.mid * 120) }
  on draw {
    let cx = w / 2
    let cy = h / 2
    let r0 = min(w, h) * 0.25
    for i in 0..64 {
      let a = rad(i * 360 / 64 + spin)
      let v = audio.fft[i]
      stroke(hsv(200 + v * 140, 0.8, 1), 3)
      line(cx + cos(a) * r0, cy + sin(a) * r0,
           cx + cos(a) * (r0 + 10 + v * r0), cy + sin(a) * (r0 + 10 + v * r0))
    }
  }
}</pre>
<h3>Кнопка</h3>
<pre>widget PlayButton {
  on click { toggle() }
  on draw {
    fill(hover ? theme.accent2 : theme.accent)
    circle(w / 2, h / 2, min(w, h) / 2)
    fill(theme.bg)
    text(player.playing ? "❚❚" : "▶", w / 2, h / 2, 18, align: "center")
  }
}</pre>
<h3>Частицы на бит</h3>
<pre>widget Sparks {
  state parts = []
  on beat(p) {
    for i in 0..floor(10 + p * 30) {
      let a = rand(0, TAU)
      let s = rand(60, 260) * p
      push(parts, {x: w / 2, y: h / 2, vx: cos(a) * s, vy: sin(a) * s, life: 1})
    }
  }
  on frame(dt) {
    let keep = []
    for q in parts {
      q.vy += 180 * dt                 // гравитация
      q.x += q.vx * dt
      q.y += q.vy * dt
      q.life -= dt
      if q.life > 0 { push(keep, q) }
    }
    parts = keep
  }
  on draw {
    blend("add")
    for q in parts { glow(q.x, q.y, 10, with_alpha(theme.accent, q.life)) }
    blend("normal")
  }
}</pre>
<h3>Перетаскиваемый ползунок громкости</h3>
<pre>widget Volume {
  on press(x, y) { set_volume(clamp(x / w)) }
  on drag(x, y) { set_volume(clamp(x / w)) }
  on wheel(d) { set_volume(clamp(player.volume + d * 0.05)) }
  on draw {
    fill("#ffffff22"); rect(0, h / 2 - 3, w, 6, 3)
    fill(theme.accent); rect(0, h / 2 - 3, w * player.volume, 6, 3)
    circle(w * player.volume, h / 2, 8)
  }
}</pre>
<p>Целая тема на EchoScript — встроенный скрипт <b>Loom</b> (откройте его в списке слева).</p>
"""),
]


def _sections():
    try:
        import i18n
        if i18n.LANG == "en":
            import dsl_docs_en
            return dsl_docs_en.SECTIONS
    except Exception:                                      # noqa: BLE001
        pass
    return SECTIONS


def html() -> str:
    return CSS + "".join(body for _, body in _sections())


def toc() -> list[str]:
    return [title for title, _ in _sections()]
