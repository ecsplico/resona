import ctypes
import types
from unittest.mock import patch

from resona_engine_faster_whisper._cuda_libs import preload_cuda_libs


def test_preload_is_noop_when_packages_absent():
    """No nvidia.* packages installed -> preload silently does nothing."""
    with patch(
        "resona_engine_faster_whisper._cuda_libs.importlib.util.find_spec",
        return_value=None,
    ):
        with patch("resona_engine_faster_whisper._cuda_libs.ctypes.CDLL") as cdll:
            preload_cuda_libs()
            cdll.assert_not_called()


def test_preload_loads_existing_libs(tmp_path):
    """When an nvidia lib dir + .so exist, the .so is CDLL-loaded RTLD_GLOBAL."""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "libcublas.so.12").touch()
    (lib_dir / "libcudnn.so.9").touch()

    fake_spec = types.SimpleNamespace(submodule_search_locations=[str(lib_dir)])

    with patch(
        "resona_engine_faster_whisper._cuda_libs.importlib.util.find_spec",
        return_value=fake_spec,
    ):
        with patch("resona_engine_faster_whisper._cuda_libs.ctypes.CDLL") as cdll:
            preload_cuda_libs()
            calls = cdll.call_args_list
            assert [c.args[0] for c in calls] == [
                str(lib_dir / "libcublas.so.12"),
                str(lib_dir / "libcudnn.so.9"),
            ]
            for c in calls:
                assert c.kwargs.get("mode") == ctypes.RTLD_GLOBAL


def test_preload_skips_when_so_file_missing(tmp_path):
    """nvidia lib dir exists but the .so is absent -> CDLL not called."""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()

    fake_spec = types.SimpleNamespace(submodule_search_locations=[str(lib_dir)])

    with patch(
        "resona_engine_faster_whisper._cuda_libs.importlib.util.find_spec",
        return_value=fake_spec,
    ):
        with patch("resona_engine_faster_whisper._cuda_libs.ctypes.CDLL") as cdll:
            preload_cuda_libs()
            cdll.assert_not_called()


def test_preload_swallows_oserror(tmp_path):
    """A failing CDLL load is logged, not raised."""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "libcublas.so.12").touch()
    (lib_dir / "libcudnn.so.9").touch()

    fake_spec = types.SimpleNamespace(submodule_search_locations=[str(lib_dir)])

    with patch(
        "resona_engine_faster_whisper._cuda_libs.importlib.util.find_spec",
        return_value=fake_spec,
    ):
        with patch(
            "resona_engine_faster_whisper._cuda_libs.ctypes.CDLL",
            side_effect=OSError("boom"),
        ):
            preload_cuda_libs()  # must not raise
