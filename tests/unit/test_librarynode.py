"""The library node: seeding, installing, removing.

The seeding branch is the one that matters. CLibraryDirectory reads the
profile's node folder *instead of* Kodi's defaults rather than merging them, so
creating that folder with only our node in it would leave the profile without
Artists, Albums or Songs.
"""

import pytest

from musicrestore import librarynode


@pytest.fixture
def vfs(monkeypatch):
    """An in-memory stand-in for the bits of xbmcvfs the module uses."""

    class Vfs:
        def __init__(self) -> None:
            self.files = {}
            self.dirs = set()
            self.system = {
                "albums.xml": "<node/>",
                "artists.xml": "<node/>",
                "songs.xml": "<node/>",
            }
            self.system_dirs = {"top100": ["albums.xml", "songs.xml"]}
            self.copy_fails = False
            self.mkdirs_fails = False

        # -- the xbmcvfs surface librarynode uses -------------------------
        def exists(self, path):
            return path in self.files or path.rstrip("/") + "/" in self.dirs

        def mkdirs(self, path):
            if self.mkdirs_fails:
                return False
            self.dirs.add(path.rstrip("/") + "/")
            return True

        def listdir(self, path):
            if path == librarynode.SYSTEM_NODES:
                return list(self.system_dirs), list(self.system)
            for name, children in self.system_dirs.items():
                if path == "%s%s/" % (librarynode.SYSTEM_NODES, name):
                    return [], list(children)
            return [], []

        def copy(self, src, dst):
            if self.copy_fails:
                return False
            self.files[dst] = "copied"
            return True

        def delete(self, path):
            return self.files.pop(path, None) is not None

        def File(self, path, mode="r"):  # noqa: N802 - mirrors the xbmcvfs name
            store = self.files
            outer = self

            class Handle:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def write(self, content):
                    store[path] = content
                    return True

                def read(self):
                    return outer.files.get(path, "")

            return Handle()

    fake = Vfs()
    monkeypatch.setattr(librarynode, "xbmcvfs", fake)
    return fake


class TestInstall:
    def test_writes_the_node_when_the_profile_folder_exists(self, vfs):
        vfs.dirs.add(librarynode.PROFILE_NODES)
        assert librarynode.install() is True
        assert librarynode.NODE_PATH in vfs.files

    def test_node_points_at_the_plugin_and_is_a_folder(self, vfs):
        vfs.dirs.add(librarynode.PROFILE_NODES)
        librarynode.install()
        xml = vfs.files[librarynode.NODE_PATH]
        assert "<path>plugin://script.music.restore/</path>" in xml
        assert 'type="folder"' in xml
        assert "<label>Recent queues</label>" in xml
        assert 'order="%d"' % librarynode.NODE_ORDER in xml

    def test_seeds_kodi_defaults_before_creating_the_folder(self, vfs):
        # Nothing exists yet: the defaults must be copied in, or the profile
        # would end up with this node and nothing else.
        assert librarynode.install() is True
        for name in ("albums.xml", "artists.xml", "songs.xml"):
            assert librarynode.PROFILE_NODES + name in vfs.files
        assert librarynode.NODE_PATH in vfs.files

    def test_seeding_covers_node_subfolders(self, vfs):
        librarynode.install()
        assert librarynode.PROFILE_NODES + "top100/albums.xml" in vfs.files

    def test_does_not_write_the_node_if_seeding_fails(self, vfs):
        vfs.copy_fails = True
        assert librarynode.install() is False
        assert librarynode.NODE_PATH not in vfs.files

    def test_does_not_write_the_node_if_the_folder_cannot_be_made(self, vfs):
        vfs.mkdirs_fails = True
        assert librarynode.install() is False
        assert librarynode.NODE_PATH not in vfs.files

    def test_is_idempotent(self, vfs):
        vfs.dirs.add(librarynode.PROFILE_NODES)
        librarynode.install()
        vfs.files[librarynode.NODE_PATH] = "SENTINEL"
        assert librarynode.install() is True
        assert vfs.files[librarynode.NODE_PATH] == "SENTINEL"

    def test_an_existing_node_never_triggers_seeding(self, vfs):
        vfs.files[librarynode.NODE_PATH] = "<node/>"
        vfs.copy_fails = True  # would fail loudly if seeding were attempted
        assert librarynode.install() is True


class TestRemove:
    def test_deletes_only_our_node(self, vfs):
        vfs.dirs.add(librarynode.PROFILE_NODES)
        vfs.files[librarynode.PROFILE_NODES + "albums.xml"] = "<node/>"
        librarynode.install()
        assert librarynode.remove() is True
        assert librarynode.NODE_PATH not in vfs.files
        assert librarynode.PROFILE_NODES + "albums.xml" in vfs.files

    def test_removing_a_missing_node_is_fine(self, vfs):
        assert librarynode.remove() is True


class TestSync:
    def test_true_installs(self, vfs):
        vfs.dirs.add(librarynode.PROFILE_NODES)
        assert librarynode.sync(True) is True
        assert librarynode.NODE_PATH in vfs.files

    def test_false_removes(self, vfs):
        vfs.dirs.add(librarynode.PROFILE_NODES)
        librarynode.install()
        assert librarynode.sync(False) is True
        assert librarynode.NODE_PATH not in vfs.files
