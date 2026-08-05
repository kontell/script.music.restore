"""The skin edit as pure text: anchors, insertion, byte-exact removal.

The fixtures mirror the real structure both supported skins share (Contuary
inherits Estuary's ``WidgetListCategories``), trimmed to the anchor regions.
A guard test runs the same assertions against the actual skin files when they
are present on the machine, so anchor drift shows up here before it shows up
as a failed patch.
"""

import os
import xml.etree.ElementTree as ET

import pytest

from musicrestore import skinpatch

INCLUDES_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<includes>
	<include name="WidgetListCategories">
		<param name="item_limit">20</param>
		<param name="additional_movie_items">false</param>
		<param name="additional_tvshow_items">false</param>
		<param name="visible">true</param>
		<definition>
			<control type="panel" id="$PARAM[list_id]">
				<include condition="$PARAM[additional_movie_items]" content="MovieSubmenuItems" />
				<include condition="$PARAM[additional_tvshow_items]" content="TVShowSubmenuItems" />
				<content target="$PARAM[widget_target]" limit="$PARAM[item_limit]">$PARAM[content_path]</content>
			</control>
		</definition>
	</include>
	<include name="MovieSubmenuItems">
		<content>
			<item>
				<label>$LOCALIZE[31146]</label>
			</item>
		</content>
	</include>
	<include name="TVShowSubmenuItems">
		<content>
			<item>
				<label>$LOCALIZE[31027]</label>
				<onclick>ActivateWindow(videos,plugin://script.embuary.info/nextaired,return)</onclick>
			</item>
		</content>
	</include>
</includes>
"""

HOME_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<window>
	<defaultcontrol>9000</defaultcontrol>
	<backgroundcolor>background</backgroundcolor>
	<controls>
		<include content="WidgetListCategories" condition="Library.HasContent(music)">
			<param name="content_path" value="library://music/"/>
			<param name="widget_header" value="$LOCALIZE[31148]"/>
			<param name="widget_target" value="music"/>
			<param name="list_id" value="7900"/>
		</include>
	</controls>
</window>
"""

SKIN_ROOTS = [
    os.path.expanduser("~/.kodi/addons/skin.contuary"),
    "/usr/share/kodi/addons/skin.estuary",
]


def apply_includes() -> str:
    text, missing = skinpatch.apply_text(
        INCLUDES_XML, skinpatch.INSERTIONS[skinpatch.INCLUDES_FILE]
    )
    assert missing == []
    return text


def apply_home() -> str:
    text, missing = skinpatch.apply_text(
        HOME_XML, skinpatch.INSERTIONS[skinpatch.HOME_FILE]
    )
    assert missing == []
    return text


class TestApply:
    def test_includes_gets_all_three_pieces(self):
        text = apply_includes()
        assert '<param name="additional_music_items">false</param>' in text
        assert 'content="MusicSubmenuItems"' in text
        assert "RunScript(script.music.restore)" in text

    def test_hook_lands_between_tvshow_hook_and_content(self):
        text = apply_includes()
        hook = text.index('condition="$PARAM[additional_music_items]"')
        assert text.index('condition="$PARAM[additional_tvshow_items]"') < hook
        assert hook < text.index("<content target=")

    def test_include_block_lands_after_tvshow_include(self):
        text = apply_includes()
        assert text.index('<include name="MusicSubmenuItems">') > text.index(
            '<include name="TVShowSubmenuItems">'
        )

    def test_home_row_asks_for_the_items(self):
        text = apply_home()
        assert '<param name="additional_music_items" value="true"/>' in text
        assert text.index("additional_music_items") > text.index("content_path")

    def test_patched_files_are_still_wellformed_xml(self):
        ET.fromstring(apply_includes())
        ET.fromstring(apply_home())

    def test_indent_matches_the_anchor(self):
        text = apply_includes()
        line = next(
            l for l in text.splitlines() if 'additional_music_items">false' in l
        )
        assert line.startswith("\t\t<param")

    def test_missing_anchor_is_named_not_ignored(self):
        broken = INCLUDES_XML.replace("TVShowSubmenuItems", "GoneItems")
        _, missing = skinpatch.apply_text(
            broken, skinpatch.INSERTIONS[skinpatch.INCLUDES_FILE]
        )
        assert "categories hook" in missing
        assert "MusicSubmenuItems include" in missing

    def test_marker_detection(self):
        assert not skinpatch.is_patched(INCLUDES_XML)
        assert skinpatch.is_patched(apply_includes())

    def test_native_music_hook_counts_as_present(self):
        assert skinpatch.needs_patch(INCLUDES_XML)
        assert not skinpatch.needs_patch(apply_includes())
        native = INCLUDES_XML.replace(
            'name="additional_tvshow_items">false',
            'name="additional_tvshow_items">false</param>\n\t\t'
            '<param name="additional_music_items">false',
            1,
        )
        assert not skinpatch.needs_patch(native)


class TestRemove:
    def test_round_trip_is_byte_exact(self):
        assert skinpatch.remove_text(apply_includes()) == INCLUDES_XML
        assert skinpatch.remove_text(apply_home()) == HOME_XML

    def test_pristine_text_passes_through(self):
        assert skinpatch.remove_text(INCLUDES_XML) == INCLUDES_XML

    def test_foreign_edits_survive_removal(self):
        text = apply_includes().replace("$LOCALIZE[31027]", "$LOCALIZE[31027] edited")
        assert "edited" in skinpatch.remove_text(text)


@pytest.mark.parametrize("root", SKIN_ROOTS)
def test_anchors_hold_in_the_real_skin(root):
    """Drift guard: run only where the skin is actually installed."""
    if not os.path.isdir(root):
        pytest.skip("skin not present on this machine")
    for rel, insertions in skinpatch.INSERTIONS.items():
        with open(os.path.join(root, rel), "r", encoding="utf-8") as handle:
            text = handle.read()
        if skinpatch.is_patched(text):
            text = skinpatch.remove_text(text)
        patched, missing = skinpatch.apply_text(text, insertions)
        assert missing == [], "%s in %s" % (missing, rel)
        assert skinpatch.remove_text(patched) == text
