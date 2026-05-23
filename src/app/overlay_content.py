"""Static content blocks used by the overlay UI."""
from __future__ import annotations


def onboarding_pages() -> list[dict]:
    """Tour pages for first-run onboarding."""
    return [
        {
            "title": "Welcome to Corenous",
            "body": "Your Mac quietly remembers what you read, "
                    "write, and copy. Local only. Press the shortcut "
                    "below from anywhere to open this overlay.",
            "shortcuts": [("⌥⌘⇧ Space", "Toggle overlay from anywhere")],
        },
        {
            "title": "Search and recall",
            "body": "⌘K jumps to the search bar. Type a few words "
                    "and Corenous searches every captured moment, "
                    "even the ones that never landed in your browser history.",
            "shortcuts": [
                ("⌘ K", "Focus the search field"),
                ("Esc", "Close the overlay"),
            ],
        },
        {
            "title": "Privacy controls",
            "body": "⌘\\ hides the overlay from screen recording and "
                    "video calls. ⌘P pauses background capture when "
                    "you need a quiet moment. Sensitive text goes to the encrypted vault.",
            "shortcuts": [
                ("⌘ \\", "Toggle stealth mode"),
                ("⌘ P", "Pause / resume capture"),
            ],
        },
    ]


def footer_shortcut_defs() -> list[tuple[str, str, str | None]]:
    """Footer chip rows: (glyph, hover_description, callback_method_name)."""
    return [
        ("⌥⌘⇧ Space", "Show / hide overlay", None),
        ("⌘ K", "Jump to search", "_activate_search_input"),
        ("⌘ P", "Pause / resume capture", "_toggle_capture_pause"),
        ("⌘ \\", "Stealth mode — hides from screen share", "_toggle_stealth"),
        ("Esc", "Close overlay", "_hide_panel"),
    ]
