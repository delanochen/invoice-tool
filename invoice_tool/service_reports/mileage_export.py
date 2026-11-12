"""Mileage-proof helpers shared by individual and batch report downloads."""
import re
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def mileage_proof_is_image(attachment):
    return Path(attachment["stored_filename"]).suffix.lower() in IMAGE_SUFFIXES


def write_mileage_documents(archive, folder, attachments, attachment_path):
    missing = []
    for attachment in attachments:
        if mileage_proof_is_image(attachment):
            continue
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", attachment["original_filename"] or "")
        name = name.strip(" .") or "mileage-proof"
        path = Path(attachment_path(attachment))
        if path.is_file():
            archive.write(path, f"{folder}/{attachment['id']}-{name}")
        else:
            missing.append(name)
    return missing
