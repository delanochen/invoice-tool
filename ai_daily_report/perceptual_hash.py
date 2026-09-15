"""AI Daily Report - Perceptual Hash Service (Phase 5)

Computes perceptual hashes (dHash/pHash) for near-duplicate detection.

CRITICAL: Perceptual hash is ONLY used for near-duplicate suppression.
It is NOT used to classify repair stages (before/during/after) - that requires Vision.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default dHash size (9x8 -> 64 bit hash)
DHASH_WIDTH = 9
DHASH_HEIGHT = 8

# Near-duplicate threshold (Hamming distance <= this = near duplicate)
NEAR_DUPLICATE_THRESHOLD = 5


def compute_dhash(image_path: Path, width: int = DHASH_WIDTH, height: int = DHASH_HEIGHT) -> Optional[str]:
    """Compute difference hash (dHash) of an image.

    dHash compares adjacent pixel brightness differences.
    Returns hex string or None if computation fails.
    """
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            # Convert to grayscale and resize
            gray = img.convert("L").resize((width, height), Image.LANCZOS)
            pixels = list(gray.getdata())
            # Compare adjacent pixels in each row
            hash_bits = []
            for row in range(height):
                for col in range(width - 1):
                    left = pixels[row * width + col]
                    right = pixels[row * width + col + 1]
                    hash_bits.append(1 if left > right else 0)
            # Convert bits to hex
            hash_int = 0
            for bit in hash_bits:
                hash_int = (hash_int << 1) | bit
            return f"{hash_int:016x}"
    except Exception as e:
        logger.debug("dHash computation failed: %s", type(e).__name__)
        return None


def hamming_distance(hash1: str, hash2: str) -> int:
    """Compute Hamming distance between two hex hash strings."""
    if not hash1 or not hash2:
        return 999
    try:
        x = int(hash1, 16)
        y = int(hash2, 16)
        return bin(x ^ y).count("1")
    except (ValueError, TypeError):
        return 999


def is_near_duplicate(hash1: str, hash2: str, threshold: int = NEAR_DUPLICATE_THRESHOLD) -> bool:
    """Check if two hashes represent near-duplicate images."""
    return hamming_distance(hash1, hash2) <= threshold


class PerceptualHashService:
    """Service for computing and comparing perceptual hashes."""

    def __init__(self, threshold: int = NEAR_DUPLICATE_THRESHOLD):
        self.threshold = threshold
        self._cache = {}  # photo_hash -> dhash

    def get_hash(self, image_path: Path, photo_hash: str = "") -> Optional[str]:
        """Get dHash for an image, with cache by photo_hash."""
        if photo_hash and photo_hash in self._cache:
            return self._cache[photo_hash]
        dhash = compute_dhash(image_path)
        if photo_hash and dhash:
            self._cache[photo_hash] = dhash
        return dhash

    def is_near_duplicate(self, hash1: str, hash2: str) -> bool:
        """Check if two hashes are near-duplicates."""
        return is_near_duplicate(hash1, hash2, self.threshold)
