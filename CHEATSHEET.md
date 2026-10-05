# Шпаргалка: команды для Терминала

Всё про дашборд MiniToo (`apps/minitoo-dashboard`). Команда `minitoo-dashboard`
доступна из любой папки. Если Терминал её не находит, запускайте полный путь:
`~/Claude\ code/Minitoo/apps/minitoo-dashboard/bin/minitoo-dashboard`.

## Каждый день

| Что нужно | Команда |
| --- | --- |
| Что сейчас делает дашборд, свежесть данных, ошибки | `minitoo-dashboard status` |
| Последние строки лога (по умолчанию 40) | `minitoo-dashboard logs` или `minitoo-dashboard logs -n 100` |
| Следить за логом в реальном времени (выход: Ctrl+C) | `tail -f ~/.minitoo-dashboard/dashboard.log` |
| Отпустить MiniToo, например для приложения Divoom на телефоне | `minitoo-dashboard pause` |
| Снова показывать дашборд | `minitoo-dashboard resume` |
| Проверить лимиты Claude прямо сейчас | `minitoo-dashboard refresh-limits` |
| Посмотреть экран без устройства (PNG в `~/.minitoo-dashboard/preview`) | `minitoo-dashboard preview` |
| То же на примерных данных | `minitoo-dashboard preview --demo` |

Когда лимит Claude или Codex (5 часов или неделя) доходит до 90%, на экране на 10
секунд появляется кадр «Claude / Лимит 5ч / 92% / сброс 1ч20м». Каждое окно
предупреждает один раз, до своего сброса.

## Если пришло уведомление «Нет доступа к Keychain»

```bash
minitoo-dashboard grant-keychain
```

Появится одно окно macOS: введите пароль и нажмите «Разрешать всегда». Фоновый
дашборд сам такое окно никогда не открывает. Пока доступа нет, лимиты Claude
обновляются только из status line (во время работы в терминальном `claude`).

## Если лимиты Claude не обновляются (`http_403` / `http_429` в статусе)

Сервер отказывает: обычно закончилась подписка (403), а частые отказы приводят к 429.
Дашборд сам делает паузы между попытками (10, 20, 40, потом 60 минут). После оплаты:

```bash
minitoo-dashboard refresh-limits
```

Если не помогло, запустите `claude` в Терминале (Claude Code обновит вход) и повторите команду.

## Если MiniToo не отвечает (`Device reply: not responding` в статусе)

Обычно это телефон: после приложения Divoom телефон держит Bluetooth-соединение с
MiniToo, даже если приложение закрыто. Выключите Bluetooth на телефоне: одного
отключения MiniToo в настройках телефона мало, телефон подключается снова. Перезагружать
MiniToo не нужно. Дашборд вернётся сам в течение нескольких минут, а сразу — после
`minitoo-dashboard pause` и `minitoo-dashboard resume`. Перезагрузите MiniToo, только
если он так и не ответил.

## Настройки

| Что нужно | Команда |
| --- | --- |
| Город для погоды | `minitoo-dashboard city "Ульяновск"` |
| Если городов несколько — выбрать номер из списка | `minitoo-dashboard city "Ульяновск" --pick 1` |
| Язык экрана | `minitoo-dashboard init --lang ru` (или `en`) |
| Градусы | `minitoo-dashboard init --temp-unit celsius` (или `fahrenheit`) |
| Откуда брать лимиты Claude | `minitoo-dashboard init --claude-limits direct` (или `statusline`) |
| Показывать лимиты Codex | `minitoo-dashboard init --codex on` (или `off`) |
| Порог предупреждения о лимите (по умолчанию 90%) | в файле настроек `LIMIT_ALERT=80` (или `off`) |
| Открыть файл настроек в TextEdit | `open -e ~/.minitoo-dashboard/config` |

Дашборд перечитывает настройки каждую секунду, перезапуск не нужен.
В Claude Code город можно поменять командой `/dashboard-city Ульяновск`.

## Фоновый процесс (launchd)

| Что нужно | Команда |
| --- | --- |
| Перезапустить (нужно после изменений в коде) | `launchctl kickstart -k gui/$(id -u)/local.minitoo.dashboard` |
| Проверить, что запущен | `launchctl print gui/$(id -u)/local.minitoo.dashboard \| grep -m1 state` |
| Остановить до следующего входа в систему | `launchctl bootout gui/$(id -u)/local.minitoo.dashboard` |
| Запустить снова после остановки | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.minitoo.dashboard.plist` |

## После обновления кода

```bash
cd ~/Claude\ code/Minitoo
git pull fork main
launchctl kickstart -k gui/$(id -u)/local.minitoo.dashboard
```

Если менялся `usage-helper` (файл `apps/minitoo-dashboard/usage-helper/UsageHelper.swift`),
пересоберите его и заново выдайте доступ:

```bash
~/Claude\ code/Minitoo/apps/minitoo-dashboard/usage-helper/build.sh
minitoo-dashboard grant-keychain
```

Проверить, что всё в порядке (тесты):

```bash
cd ~/Claude\ code/Minitoo/apps/minitoo-dashboard && python3 -m unittest discover -s tests
```

## Установка и удаление

```bash
~/Claude\ code/Minitoo/apps/minitoo-dashboard/install.sh
~/Claude\ code/Minitoo/apps/minitoo-dashboard/uninstall.sh
```

Подробности — в `apps/minitoo-dashboard/README.md` (раздел Troubleshooting).
