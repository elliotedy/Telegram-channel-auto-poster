---
name: telegram-autoposter
description: >-
  Expert skill for the Telegram Channel Auto Poster bot in this workspace.
  Activate when asked to: add commands, fix scheduler/importer bugs, add new
  media types, modify posting logic, update views/keyboards, write tests,
  deploy, or any task touching this bot's Python code.
---

# Telegram Auto Poster — Agent Skill

## Quickstart for any task

1. **Read `GEMINI.md`** in the workspace root first — it contains the full module map, DB schema, all commands, all callbacks, invariants, and gotchas.
2. Identify which module(s) are affected:
   - New command → `app/handlers.py` + `BOT_COMMANDS` list + `app/views.py`
   - New callback → `app/handlers.py` `on_callback` dispatch + `app/views.py`
   - DB change → `app/database.py` only (schema + method)
   - Posting/delivery change → `app/poster.py`
   - Folder/import change → `app/importer.py`
   - Display/keyboard change → `app/views.py`
3. After every code change, run: `.venv\Scripts\python -m ruff check . --fix && .venv\Scripts\python -m compileall -q app main.py && .venv\Scripts\python -m pytest`

---

## Adding a new command — checklist

```
[ ] Add handler function in app/handlers.py with @admin_required
[ ] Add BotCommand entry to BOT_COMMANDS list
[ ] Add CommandHandler registration in register_handlers()
[ ] Add view function in app/views.py returning (str, InlineKeyboardMarkup | None)
[ ] Add callback entries to on_callback() if the view has buttons
[ ] Add/update tests in tests/test_handlers_and_views.py
[ ] Update help_view() text
[ ] Update README.md command table
[ ] Update GEMINI.md sections 4, 5 if needed
```

---

## Posting a new media type — checklist

```
[ ] Add extension to appropriate set in app/importer.py (VIDEO/IMAGE/ANIMATION_EXTENSIONS)
[ ] Handle new mime type or extension in _extract_media() in app/handlers.py
[ ] Handle new media_type in Poster.deliver() in app/poster.py (send_* call)
[ ] Update _store_media() label dict if needed
[ ] Add test coverage
```

---

## Common code patterns

### Admin-only command handler template
```python
@admin_required
async def cmd_mycommand(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = _message_text_after_command(update)
    db = _db(context)
    # ... logic ...
    await _reply(update, "<b>Result</b>", markup)
```

### Callback handler addition (inside on_callback)
```python
elif data == "myaction":
    text, markup = await views.my_view(db)
    await _safe_edit(query, text, markup)
elif data.startswith("myaction:"):
    param = _parse_int(data.split(":", 1)[1])
    # ... logic ...
```

### View function template (app/views.py)
```python
async def my_view(db: Database) -> tuple[str, InlineKeyboardMarkup | None]:
    items = await db.list_something()
    lines = ["<b>🔧 Title</b>", ""]
    for item in items:
        lines.append(f"• {html.escape(item['name'])}")
    keyboard = [[InlineKeyboardButton("◀ Back", callback_data="menu")]]
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)
```

### Database method template (app/database.py)
```python
async def my_method(self, param: int) -> list[dict]:
    async with self._conn.execute(
        "SELECT * FROM mytable WHERE id = ?", (param,)
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
```

---

## Running the bot locally (Windows)

```powershell
# 1. Create .env from example:
copy .env.example .env
# Edit .env — set TELEGRAM_BOT_TOKEN and ADMIN_IDS

# 2. Set up venv (first time):
setup-windows.bat

# 3. Start:
start-windows.bat
# or manually:
.venv\Scripts\python main.py
```

---

## Running tests

```powershell
.venv\Scripts\python -m pytest -v
.venv\Scripts\python -m ruff check . --fix
.venv\Scripts\python -m compileall -q app main.py
```

---

## Debugging with Chrome DevTools (browser skill)

The `chrome-devtools` MCP is configured globally. Use it to:
- Inspect any web UI related to this project (Telegram Web, GitHub Actions, monitoring dashboards).
- Automate browser interactions for testing or scraping channel info.

Workflow:
1. `list_pages` — see open browser pages
2. `navigate_page` — go to URL
3. `take_snapshot` — get DOM structure + element `uid`s
4. `click` / `fill` / `evaluate_script` — interact

---

## Key invariants (never break)

- `@admin_required` on every handler that touches bot state.
- `views.py` functions are **pure** — no `await`, no DB/bot access inside them (they receive pre-fetched data as arguments).
- Always use `parse_mode="HTML"` and escape with `html.escape()`.
- `file_unique_id` is the dedup key, never `file_id`.
- `next_post_at` stored as UTC ISO-8601 string.
- Leave `realestate-php/` untouched.
