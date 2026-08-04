#!/usr/bin/env python3
"""Embed externally linked images in one or more generated DOCX packages."""

from __future__ import annotations

import argparse
import os
import tempfile
import urllib.parse
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


DOCS_DIR = Path(__file__).resolve().parent
DEFAULT_DOCX = DOCS_DIR / "金桥融通WMS操作手册.docx"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def embed_images(docx_path: Path) -> int:
    """Replace external image links with embedded media and return the count."""
    docx_path = Path(docx_path).resolve()
    with zipfile.ZipFile(docx_path, "r") as source:
        files = {name: source.read(name) for name in source.namelist()}

    rel_name = "word/_rels/document.xml.rels"
    rel_root = ET.fromstring(files[rel_name])
    document_xml = files["word/document.xml"].decode("utf-8")
    media: dict[str, bytes] = {}
    extensions: set[str] = set()
    image_index = 1

    for relationship in rel_root.findall(f"{{{REL_NS}}}Relationship"):
        if not relationship.get("Type", "").endswith("/image"):
            continue
        if relationship.get("TargetMode") != "External":
            continue

        target = relationship.get("Target", "")
        parsed = urllib.parse.urlparse(target)
        image_path = Path(urllib.parse.unquote(parsed.path))
        if not image_path.is_file():
            raise FileNotFoundError(f"Missing linked image: {image_path}")

        extension = image_path.suffix.lower().lstrip(".")
        media_name = f"manual-image-{image_index}.{extension}"
        image_index += 1
        extensions.add(extension)
        media[f"word/media/{media_name}"] = image_path.read_bytes()

        relationship.set("Target", f"media/{media_name}")
        relationship.attrib.pop("TargetMode", None)
        relationship_id = relationship.get("Id")
        document_xml = document_xml.replace(
            f'r:link="{relationship_id}"', f'r:embed="{relationship_id}"'
        )

    if not media:
        return 0

    ET.register_namespace("", REL_NS)
    files[rel_name] = ET.tostring(rel_root, encoding="utf-8", xml_declaration=True)
    files["word/document.xml"] = document_xml.encode("utf-8")

    content_root = ET.fromstring(files["[Content_Types].xml"])
    existing = {
        node.get("Extension", "").lower()
        for node in content_root.findall(f"{{{CONTENT_NS}}}Default")
    }
    content_types = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "svg": "image/svg+xml",
    }
    for extension in sorted(extensions - existing):
        ET.SubElement(
            content_root,
            f"{{{CONTENT_NS}}}Default",
            Extension=extension,
            ContentType=content_types.get(extension, "application/octet-stream"),
        )
    ET.register_namespace("", CONTENT_NS)
    files["[Content_Types].xml"] = ET.tostring(
        content_root, encoding="utf-8", xml_declaration=True
    )

    handle, temp_name = tempfile.mkstemp(suffix=".docx", dir=docx_path.parent)
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as target_zip:
            for name, data in files.items():
                target_zip.writestr(name, data)
            for name, data in media.items():
                target_zip.writestr(name, data)
        os.replace(temp_path, docx_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return len(media)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", nargs="*", type=Path, default=[DEFAULT_DOCX])
    args = parser.parse_args()
    for path in args.docx:
        count = embed_images(path)
        print(f"Embedded {count} images in {path}")


if __name__ == "__main__":
    main()
