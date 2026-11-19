# Участие в разработке MacAgent OS

[English](CONTRIBUTING.md) · [Français](CONTRIBUTING.fr.md) · [Русский](CONTRIBUTING.ru.md) · [简体中文](CONTRIBUTING.zh-CN.md)

Спасибо за помощь с бета-версией. Выберите [открытую задачу](https://github.com/zakos95/MacAgentOS/issues) и оставьте в ней комментарий до начала работы, чтобы избежать дублирования. Задачи с меткой [`good first issue`](https://github.com/zakos95/MacAgentOS/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) подходят для первого вклада.

Приложение SwiftUI находится в `NativeMacApp/`, сервер FastAPI — в `server.py`, MCP — в `mcp_hub.py`, Markdown-скиллы — в `claude_skills.py`, команды агентов — в `team_runtime.py`. См. [README](README.ru.md) и руководства [MCP](docs/MCP.md) и [скиллов и команд](docs/SKILLS-AND-TEAMS.md).

## Запуск проверок

Нужны Python 3.12 и Swift 6. В корне репозитория:

```sh
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r requirements.txt
MACAGENT_APP_SUPPORT_DIR=/tmp/macagent-contributor .venv312/bin/python -m unittest discover -s tests
swift test --package-path NativeMacApp
```

Сервер запускается командой `.venv312/bin/python server.py`. Ollama не требуется для автоматических тестов. Для проверки с реальной моделью установите её локально. Не публикуйте ключи API и частные файлы в коммитах или задачах.

## Pull request

Создайте ветку от `main`, ограничьте изменение одной задачей, опишите поведение до и после, перечислите выполненные проверки. Если ошибка может повториться, добавьте регрессионный тест. Для интерфейса приложите снимок экрана. При проверке на Mac укажите версию macOS, архитектуру и модель компьютера.

Утверждения о совместимости с Claude должны ссылаться на актуальную документацию Claude или MCP и точно перечислять поддерживаемые форматы и ограничения. Проблемы безопасности сообщайте лично сопровождающему через профиль GitHub, не размещая секреты в публичной задаче.

Код публикуется по [лицензии MIT](LICENSE).
