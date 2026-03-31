"""Structural tests for the CLI menu — no I/O, no subprocess calls."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from menu import GROUPS


def _all_items():
    return [item for _, items in GROUPS for item in items]


def test_groups_is_nonempty():
    assert len(GROUPS) > 0, "GROUPS must not be empty"


def test_each_group_has_header_and_items():
    for header, items in GROUPS:
        assert isinstance(header, str) and header, \
            f"Group header must be a non-empty string, got {header!r}"
        assert isinstance(items, list) and len(items) > 0, \
            f"Group '{header}' must have at least one item"


def test_each_item_has_label_and_cmd():
    for item in _all_items():
        assert 'label' in item, f"Item missing 'label': {item}"
        assert 'cmd' in item, f"Item missing 'cmd': {item}"
        assert isinstance(item['label'], str) and item['label'], \
            f"Item label must be a non-empty string: {item}"
        assert isinstance(item['cmd'], list) and len(item['cmd']) > 0, \
            f"Item cmd must be a non-empty list: {item}"


def test_cmd_scripts_exist():
    """Every cmd must reference a src/ script that actually exists."""
    import pathlib
    project_root = pathlib.Path(__file__).parent.parent
    for item in _all_items():
        cmd = item['cmd']
        assert cmd[0] == 'python', \
            f"Expected cmd[0]=='python', got {cmd[0]!r} in {item}"
        script = project_root / cmd[1]
        assert script.exists(), \
            f"Script not found: {cmd[1]} (from item '{item['label']}')"


def test_total_item_count():
    assert len(_all_items()) == 14, \
        f"Expected 14 menu items, got {len(_all_items())}"
