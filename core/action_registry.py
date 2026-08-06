"""
REX Action Registration
Registers all built-in actions with the central dispatcher.
Import this module once at startup; each register() call adds one action.
"""

from core.dispatcher import dispatcher
from core.error_handler import log_error


def register_all_actions():
    """Register every built-in REX action with the dispatcher."""

    # ── open_app ─────────────────────────────────────────────────────────
    from actions.open_app import open_app
    dispatcher.register(
        name="open_app",
        description=(
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        },
        handler=open_app,
        default_result="Opened app.",
    )

    # ── web_search ───────────────────────────────────────────────────────
    from actions.web_search import web_search as web_search_action
    dispatcher.register(
        name="web_search",
        description="Searches the web for any information.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        },
        handler=web_search_action,
    )

    # ── weather_report ───────────────────────────────────────────────────
    from actions.weather_report import weather_action
    dispatcher.register(
        name="weather_report",
        description="Gives the weather report to user",
        parameters={
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        },
        handler=weather_action,
        default_result="Weather delivered.",
    )

    # ── send_message ─────────────────────────────────────────────────────
    from actions.send_message import send_message
    dispatcher.register(
        name="send_message",
        description="Sends a text message via WhatsApp, Telegram, Instagram DMs, or other messaging platform. Can also upload media to Instagram when mode=upload and media_path is supplied.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name for DMs"},
                "message_text": {"type": "STRING", "description": "The message to send or Instagram caption"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, Instagram, etc."},
                "mode":         {"type": "STRING", "description": "dm | upload (Instagram only; default: dm)"},
                "media_path":   {"type": "STRING", "description": "Optional image/video path for Instagram uploads"}
            },
            "required": ["platform"]
        },
        handler=send_message,
        default_result="Message sent.",
    )

    # ── reminder ─────────────────────────────────────────────────────────
    from actions.reminder import reminder
    dispatcher.register(
        name="reminder",
        description="Sets a timed reminder using Windows Task Scheduler.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        },
        handler=reminder,
        default_result="Reminder set.",
    )

    # ── youtube_video ────────────────────────────────────────────────────
    from actions.youtube_video import youtube_video
    dispatcher.register(
        name="youtube_video",
        description=(
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        },
        handler=youtube_video,
    )

    # ── screen_process ───────────────────────────────────────────────────
    from actions.screen_processor import screen_process
    dispatcher.register(
        name="screen_process",
        description=(
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        },
        handler=screen_process,
        dispatch="thread",
        default_result="Vision module activated. Stay completely silent — vision module will speak directly.",
    )

    # ── computer_settings ────────────────────────────────────────────────
    from actions.computer_settings import computer_settings
    dispatcher.register(
        name="computer_settings",
        description=(
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        },
        handler=computer_settings,
    )

    # ── smart_home_control ───────────────────────────────────────────────
    dispatcher.register(
        name="smart_home_control",
        description=(
            "Controls connected smart-home devices such as Atomberg fans and TP-Link Kasa lights/plugs. "
            "Use when the user asks to turn devices on or off, set fan speed, change brightness, or control a room."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING", "description": "Natural language smart-home command"}
            },
            "required": ["command"]
        },
        handler=None,  # handled by dispatch="smart_home"
        dispatch="smart_home",
    )

    # ── browser_control ──────────────────────────────────────────────────
    from actions.browser_control import browser_control
    dispatcher.register(
        name="browser_control",
        description=(
            "Controls the web browser. Use for: opening websites, searching the web, "
            "navigating pages, clicking elements, filling forms, scrolling, tabs, back/forward, "
            "refreshing, and any web-based task."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | navigate | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | press | back | forward | refresh | open_tab | new_tab | switch_tab | list_tabs | close"},
                "url":         {"type": "STRING", "description": "URL for go_to action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "key":         {"type": "STRING", "description": "Key name for press action"},
                "tab":         {"type": "INTEGER", "description": "1-based tab index for switch_tab"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
            },
            "required": ["action"]
        },
        handler=browser_control,
    )

    # ── file_controller ──────────────────────────────────────────────────
    from actions.file_controller import file_controller
    dispatcher.register(
        name="file_controller",
        description=(
            "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage, "
            "and organizing a desktop or any folder into subfolders by type/date."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | organize_folder | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
                "mode":        {"type": "STRING", "description": "by_type or by_date for organize actions"},
            },
            "required": ["action"]
        },
        handler=file_controller,
    )

    # ── desktop_control ──────────────────────────────────────────────────
    from actions.desktop import desktop_control
    dispatcher.register(
        name="desktop_control",
        description="Controls the desktop: wallpaper, organize, clean, list, stats.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        },
        handler=desktop_control,
    )

    # ── agent_task ───────────────────────────────────────────────────────
    dispatcher.register(
        name="agent_task",
        description="Executes complex multi-step tasks requiring multiple tools.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "The task goal"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        },
        handler=None,  # handled by dispatch="agent_task"
        dispatch="agent_task",
    )

    # ── computer_control ─────────────────────────────────────────────────
    from actions.computer_control import computer_control
    dispatcher.register(
        name="computer_control",
        description="Provides direct computer control: typing, clicking, hotkeys, scrolling, mouse movement, screenshots, and element detection.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description"},
                "value":       {"type": "STRING", "description": "Optional value"},
            },
            "required": []
        },
        handler=computer_control,
    )

    # ── game_updater ─────────────────────────────────────────────────────
    from actions.game_updater import game_updater
    dispatcher.register(
        name="game_updater",
        description="Handles Steam and Epic Games tasks: installing, updating, downloading, listing, and scheduling.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "install | update | download | list | schedule"},
                "game":    {"type": "STRING", "description": "Game name"},
                "platform": {"type": "STRING", "description": "steam | epic"},
            },
            "required": []
        },
        handler=game_updater,
        needs_speak=True,
    )

    # ── flight_finder ────────────────────────────────────────────────────
    from actions.flight_finder import flight_finder
    dispatcher.register(
        name="flight_finder",
        description="Searches Google Flights and speaks the best options.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        },
        handler=flight_finder,
    )

    # ── system_monitor ───────────────────────────────────────────────────
    from actions.system_monitor import system_monitor
    dispatcher.register(
        name="system_monitor",
        description=(
            "Shows real-time system health: CPU usage, RAM/disk usage, network status, "
            "and running processes. Use when the user asks about system health, "
            "performance, resource usage, what's running, or wants to debug REX's own health."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "scope": {
                    "type": "STRING",
                    "description": "What to show: full (default), cpu, memory, disk, network, processes, summary"
                },
                "top_n": {
                    "type": "INTEGER",
                    "description": "Number of top processes to list (default: 15)"
                }
            },
            "required": []
        },
        handler=system_monitor,
    )

    # ── file_processor ───────────────────────────────────────────────────
    from actions.file_processor import file_processor
    dispatcher.register(
        name="file_processor",
        description=(
            "Processes any file that the user has uploaded or dropped onto the interface. "
            "Use this when the user refers to an uploaded file and wants an action on it. "
            "Supports: images (describe/ocr/resize/compress/convert), "
            "PDFs (summarize/extract_text/to_word), "
            "text files (summarize/fix/reformat/translate), "
            "CSV/Excel (analyze/stats/filter/sort/convert), "
            "JSON/XML (validate/format/analyze), "
            "code files (explain/review/fix/optimize/run/document/test), "
            "audio (transcribe/trim/convert/info), "
            "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
            "archives (list/extract), "
            "presentations (summarize/extract_text). "
            "ALWAYS call this tool when a non-Word file has been uploaded and the user gives a command about it. "
            "If the user's command is ambiguous, pick the most logical action for that file type."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "file_path": {"type": "STRING", "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."},
                "action": {"type": "STRING", "description": "What to do with the file."},
                "instruction": {"type": "STRING", "description": "Free-form instruction if action doesn't cover it."},
                "format": {"type": "STRING", "description": "Target format for conversion."},
                "width":     {"type": "INTEGER", "description": "Target width for image resize"},
                "height":    {"type": "INTEGER", "description": "Target height for image resize"},
                "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize"},
                "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
                "start":     {"type": "STRING",  "description": "Start time for trim"},
                "end":       {"type": "STRING",  "description": "End time for trim"},
                "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction"},
                "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
                "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
                "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
                "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort"},
                "save":      {"type": "BOOLEAN", "description": "Save result to file"},
                "destination": {"type": "STRING", "description": "Output folder for archive extract"},
            },
            "required": []
        },
        handler=file_processor,
        needs_speak=True,
    )

    # ── presentation_builder ─────────────────────────────────────────────
    from actions.office_builder import create_presentation, create_spreadsheet
    dispatcher.register(
        name="presentation_builder",
        description=(
            "Creates editable PowerPoint presentations (.pptx) from a structured slide outline. "
            "Use when the user asks for a deck, slideshow, presentation, pitch deck, or report slides."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "title":     {"type": "STRING", "description": "Presentation title"},
                "subtitle":  {"type": "STRING", "description": "Optional subtitle or audience line"},
                "theme":     {"type": "STRING", "description": "Optional theme: neon, corporate, luxury, academic, sunset, creative"},
                "outline":   {"type": "STRING", "description": "Slide-by-slide outline."},
                "output_path": {"type": "STRING", "description": "Optional output path for the .pptx"},
                "auto_open": {"type": "BOOLEAN", "description": "Open the file after creating it (default: true)"},
            },
            "required": ["title"]
        },
        handler=create_presentation,
        default_result="Presentation created.",
    )

    # ── spreadsheet_builder ──────────────────────────────────────────────
    dispatcher.register(
        name="spreadsheet_builder",
        description=(
            "Creates editable Excel workbooks (.xlsx) from structured sheet data. "
            "Use for trackers, tables, analysis workbooks, budgets, planners, and other spreadsheet requests."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "title":      {"type": "STRING", "description": "Workbook title"},
                "output_path": {"type": "STRING", "description": "Optional output path for the .xlsx"},
                "auto_open":  {"type": "BOOLEAN", "description": "Open the file after creating it (default: true)"},
            },
            "required": ["title"]
        },
        handler=create_spreadsheet,
        default_result="Spreadsheet created.",
    )

    # ── word_document ────────────────────────────────────────────────────
    from actions.docx_tools import word_document
    dispatcher.register(
        name="word_document",
        description="Creates and edits Word documents (.docx).",
        parameters={
            "type": "OBJECT",
            "properties": {
                "file_path": {"type": "STRING", "description": "Path to existing .docx file"},
                "action":    {"type": "STRING", "description": "create | edit | read | summarize"},
                "content":   {"type": "STRING", "description": "Document content or instructions"},
            },
            "required": []
        },
        handler=word_document,
        needs_speak=True,
    )

    # ── pdf_document ─────────────────────────────────────────────────────
    from actions.pdf_tools import create_pdf
    dispatcher.register(
        name="pdf_document",
        description="Creates and processes PDF documents.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "file_path": {"type": "STRING", "description": "Path to PDF file"},
                "action":    {"type": "STRING", "description": "create | summarize | extract_text"},
                "content":   {"type": "STRING", "description": "Content for create action"},
            },
            "required": []
        },
        handler=create_pdf,
        default_result="PDF created.",
    )

    # ── save_memory (handled specially in main.py, but declare here) ─────
    dispatcher.register(
        name="save_memory",
        description="Saves a fact about the user to long-term memory.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "category": {"type": "STRING", "description": "Memory category: personal, preferences, schedule, notes"},
                "key":      {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value":    {"type": "STRING", "description": "Concise value in English"},
            },
            "required": ["category", "key", "value"]
        },
        handler=None,  # handled specially in main.py
        dispatch="custom",
    )

    # ── email_manager ───────────────────────────────────────────────────
    from actions.email_manager import gmail_read_emails, gmail_search_emails, gmail_compose_email
    dispatcher.register(
        name="email_manager",
        description=(
            "Manages email via Gmail or Outlook. Read, search, compose, send, and organize emails. "
            "Use when the user wants to check inbox, read emails, send an email, search emails, "
            "or manage their mailbox."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Action: read, search, compose, send, list_folders, move"
                },
                "provider": {
                    "type": "STRING",
                    "description": "Email provider: gmail or outlook (default: gmail)"
                },
                "query": {
                    "type": "STRING",
                    "description": "Search query or filter (for read/search actions)"
                },
                "to": {
                    "type": "STRING",
                    "description": "Recipient email address (for compose/send)"
                },
                "subject": {
                    "type": "STRING",
                    "description": "Email subject (for compose/send)"
                },
                "body": {
                    "type": "STRING",
                    "description": "Email body text (for compose/send)"
                },
            },
            "required": ["action"]
        },
        handler=None,
        dispatch="plugin",
    )

    # ── ocr_tool ─────────────────────────────────────────────────────────
    from actions.ocr_tool import handle_ocr_tool
    dispatcher.register(
        name="ocr_tool",
        description=(
            "Extracts text from images, screenshots, PDFs, and scanned documents using OCR. "
            "Supports multi-language detection and batch processing. "
            "Use when the user wants to read text from an image, screenshot, or document."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Action: extract_image, extract_pdf, screenshot, batch, detect_language, save"
                },
                "path": {
                    "type": "STRING",
                    "description": "File path to the image or PDF"
                },
                "language": {
                    "type": "STRING",
                    "description": "OCR language code (default: eng)"
                },
            },
            "required": ["action"]
        },
        handler=None,
        dispatch="plugin",
    )

    # ── deep_research ─────────────────────────────────────────────────────
    from actions.deep_research import research_topic
    dispatcher.register(
        name="deep_research",
        description=(
            "Performs deep multi-source web research with crawling, summarization, and citations. "
            "Crawls multiple sources, extracts key information, and produces cited reports. "
            "Use when the user wants in-depth research on a topic, competitor analysis, or trend report."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Research topic or question"
                },
                "num_sources": {
                    "type": "INTEGER",
                    "description": "Number of sources to research (default: 5)"
                },
                "depth": {
                    "type": "STRING",
                    "description": "Research depth: standard, deep, or quick (default: standard)"
                },
            },
            "required": ["query"]
        },
        handler=research_topic,
    )

    # ── calendar_sync ─────────────────────────────────────────────────────
    from actions.calendar_sync import (
        google_calendar_list_events, google_calendar_create_event,
        google_calendar_today, outlook_calendar_list_events
    )
    dispatcher.register(
        name="calendar_sync",
        description=(
            "Manages calendar events via Google Calendar or Outlook. "
            "Create, update, delete, list, and query calendar events. "
            "Use when the user wants to check their schedule, create meetings, "
            "or manage calendar events."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Action: list, create, update, delete, today, free_busy"
                },
                "provider": {
                    "type": "STRING",
                    "description": "Calendar provider: google or outlook (default: google)"
                },
                "title": {
                    "type": "STRING",
                    "description": "Event title (for create)"
                },
                "start": {
                    "type": "STRING",
                    "description": "Event start time ISO format (for create/update)"
                },
                "end": {
                    "type": "STRING",
                    "description": "Event end time ISO format (for create/update)"
                },
                "days": {
                    "type": "INTEGER",
                    "description": "Number of days to look ahead (for list, default: 7)"
                },
            },
            "required": ["action"]
        },
        handler=None,
        dispatch="plugin",
    )

    # ── shutdown_rex (handled specially in dispatcher) ────────────────────
    dispatcher.register(
        name="shutdown_rex",
        description="Shuts down REX gracefully.",
        parameters={
            "type": "OBJECT",
            "properties": {},
            "required": []
        },
        handler=None,  # handled by dispatch="custom" in dispatcher
        dispatch="custom",
    )

    print(f"[Dispatcher] Registered {len(dispatcher.names())} actions: {', '.join(dispatcher.names())}")
