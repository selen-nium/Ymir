"""Interchangeable object proposal specialists; no references enter inference."""
from .detectors import detect, map_proposals, Sol, Kimi, RFDetr

__all__ = ['detect', 'map_proposals', 'Sol', 'Kimi', 'RFDetr']
