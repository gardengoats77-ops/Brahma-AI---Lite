"""Line-based integration of dispatcher into main.py."""

with open('main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

total = len(lines)
print(f"Original: {total} lines")

# ── Step 1: Add new imports after line 56 (from plugin_manager import PluginManager) ──
# Line 56 (0-indexed: 55) has "from plugin_manager import PluginManager"
new_imports = [
    "from core.error_handler import log_error, handle_errors, get_logger\n",
    "from core.dispatcher import dispatcher\n",
    "from core.action_registry import register_all_actions\n",
]

# Find the plugin_manager import line
insert_after = None
for i, line in enumerate(lines):
    if 'from plugin_manager import PluginManager' in line:
        insert_after = i
        break

if insert_after is None:
    print("ERROR: plugin_manager import not found")
    exit(1)

lines = lines[:insert_after+1] + new_imports + lines[insert_after+1:]
print(f"Step 1: Added imports after line {insert_after+1}")

# ── Step 2: Remove old action imports (lines 28-51, 0-indexed 27-50) ──────
# These are the "from actions.X import Y" lines that are now in action_registry.py
# We need to remove them but keep meeting_assistant, website_builder, attention_monitor, daily_briefing
IMPORTS_TO_REMOVE = [
    "from actions.file_processor import file_processor",
    "from actions.flight_finder     import flight_finder",
    "from actions.open_app          import open_app",
    "from actions.weather_report    import weather_action",
    "from actions.send_message      import send_message",
    "from actions.reminder          import reminder",
    "from actions.computer_settings import computer_settings",
    "from actions.screen_processor  import screen_process",
    "from actions.youtube_video     import youtube_video",
    "from actions.desktop           import desktop_control",
    "from actions.browser_control   import browser_control",
    "from actions.file_controller   import file_controller",
    "from actions.docx_tools        import word_document",
    "from actions.pdf_tools         import create_pdf",
    "from actions.web_search        import web_search as web_search_action",
    "from actions.computer_control  import computer_control",
    "from actions.game_updater      import game_updater",
]

# Rebuild lines, skipping the ones we want to remove
# Also adjust indices since we already inserted new imports
removed = 0
new_lines = []
skip_next_blank = False
for line in lines:
    stripped = line.strip()
    if any(stripped == imp for imp in IMPORTS_TO_REMOVE):
        removed += 1
        continue
    new_lines.append(line)
lines = new_lines
print(f"Step 2: Removed {removed} old action imports")

# ── Step 3: Replace TOOL_DECLARATIONS block (lines 422-983, 0-indexed 421-982) ──
# After import removal, line numbers shifted. Find TOOL_DECLARATIONS = [
td_start = None
td_end = None
for i, line in enumerate(lines):
    if line.strip() == 'TOOL_DECLARATIONS = [' and i > 300:  # after imports area
        td_start = i
        break

if td_start is None:
    print("ERROR: TOOL_DECLARATIONS start not found")
    exit(1)

# Find the matching ] on its own line
bracket_count = 0
for i in range(td_start, len(lines)):
    for ch in lines[i]:
        if ch == '[':
            bracket_count += 1
        elif ch == ']':
            bracket_count -= 1
            if bracket_count == 0:
                td_end = i
                break
    if td_end is not None:
        break

if td_end is None:
    print("ERROR: TOOL_DECLARATIONS end not found")
    exit(1)

print(f"Step 3: TOOL_DECLARATIONS: lines {td_start+1}-{td_end+1} ({td_end-td_start+1} lines)")

# Replace the block
new_td = [
    "# Register all actions with the central dispatcher\n",
    "register_all_actions()\n",
    "TOOL_DECLARATIONS = dispatcher.get_declarations()\n",
    "\n",
]
lines = lines[:td_start] + new_td + lines[td_end+1:]
print(f"Step 3: Replaced TOOL_DECLARATIONS ({td_end-td_start+1} lines -> {len(new_td)} lines)")

# ── Step 4: Find and remove the plugin_registry merge block ───────────────
# This block is: plugin_registry.discover() ... TOOL_DECLARATIONS = TOOL_DECLARATIONS + plugin_registry.get_all_tools()
# In the original REX main.py, this uses plugin_manager, not plugin_registry
# Let's check what's there
merge_start = None
merge_end = None
for i, line in enumerate(lines):
    if 'plugin_registry.discover()' in line or 'plugin_manager.load_plugins()' in line:
        merge_start = i
        break

if merge_start is not None:
    # Find the end of this block (next blank line or class/def)
    for i in range(merge_start, min(merge_start + 20, len(lines))):
        if lines[i].strip() == '' and i > merge_start + 1:
            merge_end = i
            break
        if lines[i].strip().startswith('class ') or lines[i].strip().startswith('def '):
            merge_end = i
            break

    if merge_end is not None:
        print(f"Step 4: Plugin merge block: lines {merge_start+1}-{merge_end}")
        # Keep the plugin_manager.load_plugins() and register_rex lines
        # but remove the TOOL_DECLARATIONS merge
        new_merge = []
        for i in range(merge_start, merge_end):
            line = lines[i]
            if 'TOOL_DECLARATIONS = TOOL_DECLARATIONS + plugin_registry.get_all_tools()' in line:
                continue  # skip this line
            if '_core_tool_names' in line or '_plugin_issues' in line:
                continue  # skip these too
            if 'plugin_registry.discover()' in line:
                continue  # skip - we use action_registry now
            if 'Validation warnings' in line:
                continue
            new_merge.append(line)
        lines = lines[:merge_start] + new_merge + lines[merge_end:]
        print(f"Step 4: Cleaned plugin merge block")

# ── Step 5: Replace the dispatch elif chain ───────────────────────────────
# Find "if name == 'open_app':" in the dispatch section
dispatch_start = None
dispatch_end = None
for i, line in enumerate(lines):
    if 'if name == "open_app":' in line and i > 1000:
        dispatch_start = i
        break

if dispatch_start is None:
    print("ERROR: dispatch chain start not found")
    exit(1)

# Find "Unknown tool: {name}" line
for i in range(dispatch_start, len(lines)):
    if 'Unknown tool: {name}' in lines[i]:
        dispatch_end = i
        break

if dispatch_end is None:
    print("ERROR: dispatch chain end not found")
    exit(1)

print(f"Step 5: Dispatch chain: lines {dispatch_start+1}-{dispatch_end+1} ({dispatch_end-dispatch_start+1} lines)")

new_dispatch = [
    "            result = await dispatcher.dispatch(\n",
    "                name, args,\n",
    "                ui=self.ui,\n",
    "                speak=self.speak,\n",
    "                loop=loop,\n",
    "                smart_home_service=self._smart_home,\n",
    "                plugin_registry=None,\n",
    "            )\n",
    "\n",
]
lines = lines[:dispatch_start] + new_dispatch + lines[dispatch_end+1:]
print(f"Step 5: Replaced dispatch chain ({dispatch_end-dispatch_start+1} lines -> {len(new_dispatch)} lines)")

# ── Write ─────────────────────────────────────────────────────────────────
with open('main.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print(f"\nDone! main.py: {total} -> {len(lines)} lines (removed {total - len(lines)} lines)")
