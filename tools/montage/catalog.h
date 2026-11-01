#pragma once

// Every preset the editor offers. Filters are FFmpeg chains; "{in}", "{out}" and "{n}"
// mark multi-pad graphs that the render planner wires into the clip chain.
struct Preset { QString id,name,filter,glyph; };

static const QList<Preset> &effectCatalog(){static const QList<Preset> list{
    {"mono",EchoesText("Чёрно-белый"),"hue=s=0","◐"},{"sepia",EchoesText("Сепия"),"colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131","◑"},
    {"vintage",EchoesText("Винтаж"),"eq=contrast=1.12:saturation=0.65:brightness=0.03,vignette","▣"},{"vivid",EchoesText("Насыщенный цвет"),"eq=contrast=1.15:saturation=1.5","◉"},
    {"blur",EchoesText("Размытие"),"gblur=sigma=5","◌"},{"sharpen",EchoesText("Резкость"),"unsharp=5:5:1.2:5:5:0","◆"},{"vignette",EchoesText("Виньетка"),"vignette","◎"},
    {"noise",EchoesText("Плёночное зерно"),"noise=alls=12:allf=t+u","▒"},{"mirror",EchoesText("Зеркало"),"hflip","⇋"},{"flip",EchoesText("Перевернуть по вертикали"),"vflip","⇵"},
    {"negative",EchoesText("Негатив"),"negate","◧"},{"warm",EchoesText("Тёплый цвет"),"colorbalance=rs=.15:bs=-.1","☀"},{"cold",EchoesText("Холодный цвет"),"colorbalance=bs=.18:rs=-.08","❄"},
    {"green",EchoesText("Хромакей • зелёный"),"chromakey=0x00ff00:0.15:0.1","▩"},
    {"glitch",EchoesText("Глитч"),"rgbashift=rh=-7:bh=7:edge=wrap,noise=alls=10:allf=t","▚"},{"vhs",EchoesText("VHS-кассета"),"rgbashift=rh=3:bh=-3,noise=alls=16:allf=t,eq=saturation=1.25:contrast=1.08,gblur=sigma=0.6","▤"},
    {"pixel",EchoesText("Пикселизация"),"pixelize=w=14:h=14","▦"},{"edges",EchoesText("Контуры"),"edgedetect=mode=colormix:high=0.2","◇"},
    {"trails",EchoesText("Шлейф"),"lagfun=decay=0.93","≋"},{"ghost",EchoesText("Эхо кадров"),"tmix=frames=5","❖"},
    {"shake",EchoesText("Тряска камеры"),"crop=w='trunc(iw*0.94/2)*2':h='trunc(ih*0.94/2)*2':x='iw*0.03+iw*0.03*sin(t*37)':y='ih*0.03+ih*0.03*cos(t*29)',scale=w='trunc(iw/0.94/2)*2':h='trunc(ih/0.94/2)*2'","↯"},
    {"glow",EchoesText("Свечение"),"[{in}]split[{n}a][{n}b];[{n}b]gblur=sigma=14[{n}c];[{n}a][{n}c]blend=all_mode=screen:c3_mode=lighten[{out}]","✺"},
    {"dream",EchoesText("Мечта"),"gblur=sigma=2.5,eq=brightness=0.05:saturation=1.2,curves=preset=lighter","☁"},
    {"letterbox",EchoesText("Кинополосы"),"drawbox=x=0:y=0:w=iw:h=ih*0.12:color=black:t=fill,drawbox=x=0:y=ih*0.88:w=iw:h=ih*0.12:color=black:t=fill","▭"},
    {"denoise",EchoesText("Шумоподавление видео"),"hqdn3d=4:3:6:4","✧"},{"rgb",EchoesText("RGB-сдвиг"),"rgbashift=rh=-12:bh=12","⫶"}};return list;}

static const QList<Preset> &lookCatalog(){static const QList<Preset> list{
    {"",EchoesText("Без фильтра"),"",""},
    {"cinema",EchoesText("Кино"),"colorbalance=rs=-0.08:gs=-0.02:bs=0.12:rh=0.1:gh=0.02:bh=-0.08,eq=contrast=1.12:saturation=1.1","#2d6f86,#e0913f"},
    {"sunset",EchoesText("Закат"),"colortemperature=temperature=4200,eq=saturation=1.25:contrast=1.05","#ff8a3d,#b83b5e"},
    {"golden",EchoesText("Золотой час"),"colorbalance=rm=0.12:gm=0.04:bm=-0.1,eq=saturation=1.15","#ffc857,#e07a1f"},
    {"ice",EchoesText("Лёд"),"colortemperature=temperature=11000,eq=saturation=0.85:brightness=0.03","#bfe3ff,#4a7fb5"},
    {"night",EchoesText("Ночь"),"colortemperature=temperature=13000,eq=brightness=-0.08:saturation=0.7:contrast=1.1","#1b2a4a,#4b6a9b"},
    {"noir",EchoesText("Нуар"),"hue=s=0,eq=contrast=1.45:brightness=-0.03,vignette=PI/4","#111111,#bbbbbb"},
    {"bwsoft",EchoesText("Мягкий Ч/Б"),"hue=s=0,curves=all='0/0.08 1/0.95'","#555555,#dddddd"},
    {"retro",EchoesText("Ретро"),"curves=preset=vintage","#c2a36b,#6d8a6a"},{"cross",EchoesText("Кросс-процесс"),"curves=preset=cross_process","#d4e36a,#3a6ea5"},
    {"pastel",EchoesText("Пастель"),"eq=saturation=0.6:brightness=0.07:contrast=0.88","#f6c6d8,#bfe0e8"},
    {"juicy",EchoesText("Сочный"),"vibrance=intensity=0.6,eq=contrast=1.08","#ff3d6e,#3dd6ff"},
    {"faded",EchoesText("Выцветший"),"curves=all='0/0.12 0.5/0.52 1/0.9',eq=saturation=0.75","#9a9a8c,#d8d2c0"},
    {"neon",EchoesText("Неон"),"eq=saturation=1.9:contrast=1.25,hue=h=-12","#ff2bd6,#2bffd5"},
    {"film",EchoesText("Плёнка"),"curves=preset=medium_contrast,noise=alls=10:allf=t,eq=saturation=0.9","#7a6a50,#cdb88f"},
    {"drama",EchoesText("Драма"),"curves=preset=strong_contrast,eq=saturation=0.8","#202020,#a33a2a"},
    {"matrix",EchoesText("Матрица"),"colorchannelmixer=rr=0.6:gg=1.1:bb=0.6,eq=contrast=1.15","#0b3d0b,#5cff5c"}};return list;}

// Motion presets: one id set drives in/out animations and transitions.
static const QList<Preset> &inCatalog(){static const QList<Preset> list{{"",EchoesText("Нет"),"","—"},{"fade",EchoesText("Проявление"),"","◌"},{"slide_l",EchoesText("Въезд слева"),"","→"},{"slide_r",EchoesText("Въезд справа"),"","←"},{"slide_u",EchoesText("Снизу вверх"),"","↑"},{"slide_d",EchoesText("Сверху вниз"),"","↓"},{"zoom_in",EchoesText("Рост"),"","⊕"},{"zoom_out",EchoesText("Сжатие"),"","⊖"},{"spin",EchoesText("Вращение"),"","↻"},{"pop",EchoesText("Прыжок"),"","✦"}};return list;}
static const QList<Preset> &outCatalog(){static const QList<Preset> list{{"",EchoesText("Нет"),"","—"},{"fade",EchoesText("Растворение"),"","◌"},{"slide_l",EchoesText("Уезд влево"),"","←"},{"slide_r",EchoesText("Уезд вправо"),"","→"},{"slide_u",EchoesText("Вниз"),"","↓"},{"slide_d",EchoesText("Вверх"),"","↑"},{"zoom_in",EchoesText("Уменьшение"),"","⊖"},{"zoom_out",EchoesText("Увеличение"),"","⊕"},{"spin",EchoesText("Вращение"),"","↻"}};return list;}
static const QList<Preset> &transitionCatalog(){static const QList<Preset> list{{"",EchoesText("Без перехода"),"","—"},{"dissolve",EchoesText("Растворение"),"","◐"},{"dip",EchoesText("Через чёрный"),"","●"},{"slide_l",EchoesText("Въезд слева"),"","→"},{"slide_r",EchoesText("Въезд справа"),"","←"},{"slide_u",EchoesText("Въезд снизу"),"","↑"},{"slide_d",EchoesText("Въезд сверху"),"","↓"},{"push_l",EchoesText("Толчок влево"),"","⇐"},{"push_r",EchoesText("Толчок вправо"),"","⇒"},{"zoom_in",EchoesText("Приближение"),"","⊕"},{"zoom_out",EchoesText("Отдаление"),"","⊖"},{"spin",EchoesText("Вихрь"),"","↻"}};return list;}

static const QList<Preset> &voiceCatalog(){static const QList<Preset> list{
    {"",EchoesText("Обычный голос"),"",""},{"robot",EchoesText("Робот"),"afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75",""},
    {"chipmunk",EchoesText("Бурундук"),"rubberband=pitch=1.6",""},{"deep",EchoesText("Монстр"),"rubberband=pitch=0.68",""},{"telephone",EchoesText("Телефон"),"highpass=f=400,lowpass=f=3200",""},
    {"radio",EchoesText("Радио"),"highpass=f=250,lowpass=f=5000,acrusher=bits=10:mix=0.25",""},{"megaphone",EchoesText("Мегафон"),"highpass=f=600,lowpass=f=4000,volume=1.8,alimiter=limit=0.9",""},
    {"echo",EchoesText("Эхо"),"aecho=0.8:0.7:250:0.35",""},{"hall",EchoesText("Концертный зал"),"aecho=0.8:0.85:60|120|180:0.35|0.25|0.15",""},{"underwater",EchoesText("Под водой"),"lowpass=f=450,aecho=0.6:0.5:40:0.3",""}};return list;}

static const QList<Preset> &blendCatalog(){static const QList<Preset> list{{"normal",EchoesText("Обычный"),"",""},{"screen",EchoesText("Экран"),"","#000000"},{"addition",EchoesText("Добавление"),"","#000000"},{"lighten",EchoesText("Замена светлым"),"","#000000"},{"multiply",EchoesText("Умножение"),"","#ffffff"},{"darken",EchoesText("Замена тёмным"),"","#ffffff"},{"overlay",EchoesText("Перекрытие"),"","#808080"},{"softlight",EchoesText("Мягкий свет"),"","#808080"},{"difference",EchoesText("Разница"),"","#000000"}};return list;}

struct TextStyle { QString id,name;QJsonObject style;int size,y; };
static const QList<TextStyle> &textCatalog(){static const QList<TextStyle> list{
    {"plain",EchoesText("Обычный"),{{"color","#ffffff"},{"bold",true}},72,0},
    {"outline",EchoesText("Обводка"),{{"color","#ffffff"},{"bold",true},{"stroke",6},{"strokeColor","#000000"}},80,0},
    {"title",EchoesText("Заголовок"),{{"color","#ffffff"},{"bold",true},{"shadow",true}},120,0},
    {"subtitle",EchoesText("Субтитры"),{{"color","#ffffff"},{"box",true},{"boxColor","#000000"},{"boxOpacity",60}},46,380},
    {"meme",EchoesText("Мем"),{{"font","Impact"},{"color","#ffffff"},{"stroke",8},{"strokeColor","#000000"},{"upper",true}},96,-360},
    {"neon",EchoesText("Неон"),{{"color","#7dfcff"},{"bold",true},{"glow",true}},96,0},
    {"yellow",EchoesText("Жёлтый"),{{"color","#ffd400"},{"bold",true},{"stroke",6},{"strokeColor","#111111"}},84,0},
    {"pink",EchoesText("Розовый"),{{"color","#ff4fa3"},{"bold",true},{"stroke",5},{"strokeColor","#ffffff"}},84,0},
    {"cinema",EchoesText("Кино"),{{"font","Georgia"},{"italic",true},{"color","#e8e2d0"},{"spacing",18}},64,0},
    {"lower",EchoesText("Нижняя треть"),{{"color","#ffffff"},{"bold",true},{"box",true},{"boxColor","#e8702a"},{"boxOpacity",92},{"align","left"}},48,330},
    {"typewriter",EchoesText("Печатный"),{{"font","Consolas"},{"color","#f2f2f2"},{"box",true},{"boxColor","#141414"},{"boxOpacity",75}},52,0}};return list;}

struct ExportPreset { QString id,name,container,codec;int crf,height,fps,audio;bool loud; };
static const QList<ExportPreset> &exportCatalog(){static const QList<ExportPreset> list{
    {"youtube","YouTube • MP4 H.264","mp4","h264",18,0,0,192,false},{"social","TikTok / Reels / Shorts","mp4","h264",19,0,0,192,true},
    {"hq",EchoesText("Максимальное качество • H.265"),"mp4","h265",17,0,0,256,false},{"small",EchoesText("Маленький файл • для мессенджеров"),"mp4","h264",28,720,0,128,false},
    {"prores",EchoesText("Для монтажа • ProRes MOV"),"mov","prores",0,0,0,0,false},{"webm",EchoesText("WebM • для сайтов"),"webm","vp9",32,0,0,160,false},
    {"gif",EchoesText("GIF-анимация"),"gif","gif",0,480,15,0,false},{"mp3",EchoesText("Только звук • MP3"),"mp3","mp3",0,0,0,320,false},{"wav",EchoesText("Только звук • WAV"),"wav","wav",0,0,0,0,false}};return list;}

static QString presetFilter(const QList<Preset> &list,const QString &id){for(auto &p:list)if(p.id==id)return p.filter;return {};}
static QString presetName(const QList<Preset> &list,const QString &id){for(auto &p:list)if(p.id==id)return p.name;return {};}
