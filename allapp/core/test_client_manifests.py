import json
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_ROOTS = (
    REPO_ROOT / "wmspda",
    REPO_ROOT / "wmsownersale",
    REPO_ROOT / "wmsbossbilling",
    REPO_ROOT / "sales-miniapp",
)


def _strip_json_comments(source):
    """Remove JavaScript comments without corrupting comment-like text in strings."""
    result = []
    index = 0
    in_string = False
    escaped = False
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(source) and source[index : index + 2] != "*/":
                index += 1
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _load_jsonc(path):
    return json.loads(_strip_json_comments(path.read_text(encoding="utf-8")))


class ClientManifestStructureTests(SimpleTestCase):
    def test_pages_and_manifests_are_parseable_and_have_unique_routes(self):
        for client_root in CLIENT_ROOTS:
            with self.subTest(client=client_root.name):
                pages = _load_jsonc(client_root / "pages.json")
                manifest = _load_jsonc(client_root / "manifest.json")
                paths = [entry["path"] for entry in pages["pages"]]

                self.assertTrue(manifest)
                self.assertTrue(paths)
                self.assertEqual(len(paths), len(set(paths)))

    def test_every_declared_route_has_a_page_component(self):
        for client_root in CLIENT_ROOTS:
            pages = _load_jsonc(client_root / "pages.json")
            for entry in pages["pages"]:
                route = entry["path"]
                candidates = (
                    client_root / f"{route}.vue",
                    client_root / f"{route}.nvue",
                )
                with self.subTest(client=client_root.name, route=route):
                    self.assertTrue(
                        any(candidate.is_file() for candidate in candidates),
                        f"{client_root.name} route {route!r} has no Vue component",
                    )

    def test_tab_routes_and_icons_reference_existing_files(self):
        for client_root in CLIENT_ROOTS:
            pages = _load_jsonc(client_root / "pages.json")
            declared_paths = {entry["path"] for entry in pages["pages"]}
            for tab in pages.get("tabBar", {}).get("list", []):
                with self.subTest(client=client_root.name, tab=tab["pagePath"]):
                    self.assertIn(tab["pagePath"], declared_paths)
                    self.assertTrue((client_root / tab["iconPath"]).is_file())
                    self.assertTrue((client_root / tab["selectedIconPath"]).is_file())
