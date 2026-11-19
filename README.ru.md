# MacAgent OS — открытая бета 1.1.0-beta.1

[English](README.md) · [Français](README.fr.md) · [Русский](README.ru.md) · [简体中文](README.zh-CN.md)

MacAgent OS — ИИ-ассистент для macOS на SwiftUI с локальным сервером FastAPI. [Скачайте бета-версию для Apple Silicon](https://github.com/zakos95/MacAgentOS/releases/tag/v1.1.0-beta.1), откройте DMG и скопируйте приложение в папку Applications. В настройках выберите провайдера. Для Ollama требуется установленная локальная модель, для облачных провайдеров — собственные учётные данные.

Приложение подписано временной подписью ad hoc и не прошло нотариальное удостоверение Apple. Gatekeeper может потребовать разрешение в «Системные настройки → Конфиденциальность и безопасность». Заявлена поддержка macOS 14 и новее. Пока приложение проверено на Apple Silicon с macOS 27.2.1. Совместимость с Intel и другими версиями macOS требует проверки.

## Проверенные возможности

- Чат с Ollama и сохраняемая история бесед.
- Импорт конфигураций MCP из Claude Desktop или проекта. Поддерживаются транспорты stdio, HTTP и SSE, а также инструменты, ресурсы и промпты. [Руководство по MCP](docs/MCP.md).
- Личные и проектные Markdown-скиллы с ручным запуском, аргументами и текстовыми файлами-ссылками. [Скиллы и команды](docs/SKILLS-AND-TEAMS.md).
- Несколько текстовых агентов с независимым контекстом и общим итоговым ответом.
- Авторизация локального сервера и веб-чата, хранение данных в Application Support.

Локально пройдено 55 тестов Python и 2 теста Swift. Работа чата, скилла с дополнительным файлом и команды из двух агентов проверена на qwen3:8b. Тесты MCP подключаются к настоящим локальным серверам stdio, HTTP и SSE.

## Как помочь

Нужны разработчики macOS, Swift, Python, MCP и ИИ-агентов. Прочитайте [руководство для участников](CONTRIBUTING.md), выберите [задачу для первого вклада](https://github.com/zakos95/MacAgentOS/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) или присоединитесь к [обсуждению запуска](https://github.com/zakos95/MacAgentOS/discussions/5). При сообщении об ошибке укажите версию macOS, архитектуру Mac, модель и шаги воспроизведения.

Сейчас нужны [проверки на других Mac](https://github.com/zakos95/MacAgentOS/issues/1), [тесты GitHub Actions](https://github.com/zakos95/MacAgentOS/issues/2), [OAuth для MCP](https://github.com/zakos95/MacAgentOS/issues/3) и [управляемые инструменты для команд агентов](https://github.com/zakos95/MacAgentOS/issues/4).

## Ограничения беты

Полная совместимость со всеми скиллами и коннекторами Claude остаётся целью. Хуки и выполнение shell-команд из скиллов, размещённые плагины, агенты с инструментами и встроенный OAuth для MCP ещё не реализованы. Сейчас команды агентов возвращают текст. Облачные провайдеры и разрешения macOS требуют проверки с учётными записями участников. Сравнительных тестов, подтверждающих превосходство над Claude, нет. Качество ответов зависит от выбранной модели.

## Разработка

```sh
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
.venv312/bin/python server.py
MACAGENT_APP_SUPPORT_DIR=/tmp/macagent-tests .venv312/bin/python -m unittest discover -s tests
swift test --package-path NativeMacApp
```

Сервер слушает 127.0.0.1:8000. Для веб-чата нужен локальный токен из `~/Library/Application Support/MacAgentOS/data/api_key.txt`. Переменная `MACAGENT_APP_SUPPORT_DIR` позволяет изолировать тестовые данные. [Сборка приложения](NativeMacApp/DEPLOYMENT.md).

## Лицензия

[MIT](LICENSE).
