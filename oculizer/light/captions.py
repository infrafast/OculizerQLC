"""Shared QLC+ Virtual Console caption identity."""


def normalize_caption(caption: str) -> str:
    """Normalize case and common word separators without fuzzy matching."""
    return "".join(character for character in caption.casefold() if character.isalnum())

