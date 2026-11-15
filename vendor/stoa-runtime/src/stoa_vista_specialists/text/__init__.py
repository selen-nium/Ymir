"""Positioned source text and optional, explicitly pinned OCR."""
from .reading import read_text, model_hashes
from .association import associate_text

__all__ = ['read_text', 'model_hashes', 'associate_text']
