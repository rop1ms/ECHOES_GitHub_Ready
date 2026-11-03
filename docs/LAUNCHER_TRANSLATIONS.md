# Launcher languages

The launcher opens in English by default. Select **English** or **Русский** in the top right corner. Changes apply immediately and the launcher remembers the selected language.

To add another language:

1. Click **+ → Save English template…**.
2. Open the UTF-8 JSON file and change `name` to the language name.
3. Translate the values in `translations`. Keep the keys and placeholders such as `{version}` and `{space}` unchanged.
4. Click **+ → Load a translation…** and select the file.

Example of a partial translation:

```json
{
  "format": 1,
  "name": "Deutsch",
  "translations": {
    "header.launcher": "Launcher",
    "button.launch": "ECHOES öffnen",
    "home.installed": "Installiert · {version}"
  }
}
```

Missing entries fall back to English. The launcher validates JSON, field lengths and placeholders before applying a file. A copy is stored in the launcher's own settings; the original file need not remain on disk. Translation files contain text only and cannot run code.

All launcher widgets, painting, layout and language loading are implemented in `tools/launcher_native/launcher.cpp`. The small Python host connects the C++ surface to the existing installation backend and packages the Qt runtime into one executable. Installation details may include the original output of the bundled setup scripts.

# Языки лаунчера

По умолчанию лаунчер открывается на английском. В правом верхнем углу можно выбрать **Русский**. Язык меняется сразу и сохраняется между запусками.

Для своего перевода нажмите **+ → Сохранить английский шаблон…**, переведите значения в `translations`, укажите название языка в `name`, затем загрузите файл через **+ → Загрузить перевод…**. Ключи и подстановки `{version}`, `{space}` менять нельзя. Пропущенные строки останутся английскими.
