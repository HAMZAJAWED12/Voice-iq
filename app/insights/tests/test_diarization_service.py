"""DiarizationService: model choice, checkpoint allowlist and the mock fallback.

The service imports torch, soundfile, huggingface_hub and pyannote at module
level. Each test imports it against stand-in modules instead, so the suite
runs on the light lane and never loads a model. Whether the real pyannote
checkpoints load under the allowlist is checked by hand in a heavy
environment; these tests pin the wiring.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

_SERVICE = "app.services.diarization_service"


def _module(name: str, **attrs) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def diar(monkeypatch):
    """The service module, freshly imported against stand-in heavy modules.

    pyannote.audio is made unimportable, so no pipeline loads unless a test
    installs a fake one.
    """
    import app.services as services

    add_safe_globals = MagicMock(name="add_safe_globals")
    torch = _module(
        "torch",
        cuda=SimpleNamespace(is_available=lambda: False),
        device=lambda name: name,
        serialization=SimpleNamespace(add_safe_globals=add_safe_globals),
    )
    soundfile = _module(
        "soundfile",
        info=MagicMock(name="info"),
        read=MagicMock(name="read", side_effect=AssertionError("sf.read loads every sample into memory")),
    )
    for name, module in {
        "torch": torch,
        "soundfile": soundfile,
        "huggingface_hub": _module("huggingface_hub", login=MagicMock(name="login")),
        "pyannote.audio": None,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    # Record the real entry (or its absence) so teardown restores it, and the
    # stub-built module never outlives the test.
    monkeypatch.setitem(sys.modules, _SERVICE, None)
    del sys.modules[_SERVICE]
    monkeypatch.setattr(services, "diarization_service", None, raising=False)
    module = importlib.import_module(_SERVICE)
    return SimpleNamespace(module=module, add_safe_globals=add_safe_globals, soundfile=soundfile)


def _checkpoint_classes(monkeypatch) -> dict[str, type]:
    """Stand-ins for every class the allowlist names, installed under their real module paths."""
    classes: dict[str, type] = {}
    modules: dict[str, ModuleType] = {}
    for path in (
        "omegaconf.listconfig.ListConfig",
        "omegaconf.dictconfig.DictConfig",
        "torch.torch_version.TorchVersion",
        "pyannote.audio.core.task.Specifications",
        "pyannote.audio.core.task.Problem",
        "pyannote.audio.core.task.Resolution",
    ):
        module_name, attr = path.rsplit(".", 1)
        module = modules.setdefault(module_name, _module(module_name))
        cls = type(attr, (), {})
        setattr(module, attr, cls)
        classes[path] = cls
    for module_name, module in modules.items():
        monkeypatch.setitem(sys.modules, module_name, module)
    return classes


def test_the_default_model_is_pyannote_3_1(diar):
    assert diar.module.DiarizationService().model_id == "pyannote/speaker-diarization-3.1"


def test_no_loaded_pipeline_gives_the_mock_with_a_warning(diar):
    diar.soundfile.info.return_value = SimpleNamespace(duration=12.3456)

    segments, warnings = diar.module.DiarizationService().diarize_with_warnings("call.wav")

    assert warnings == ["DIARIZATION_UNAVAILABLE"]
    assert [(s["speaker"], s["source"], s["end"]) for s in segments] == [("SPEAKER_00", "mock", 12.346)]


def test_the_mock_reads_the_duration_from_the_header_not_the_samples(diar):
    # Two hours at 16 kHz: reading the samples would hold about 920 MB of float64.
    diar.soundfile.info.return_value = SimpleNamespace(duration=7200.0)

    segments = diar.module.DiarizationService()._mock_diarization("long.wav")

    diar.soundfile.info.assert_called_once_with("long.wav")
    diar.soundfile.read.assert_not_called()
    assert segments[0]["end"] == 7200.0


def test_a_failed_run_still_reports_the_fallback(diar):
    # Control: unchanged behaviour, so the duration works either way it is read.
    diar.soundfile.info.return_value = SimpleNamespace(duration=5.0)
    diar.soundfile.read.side_effect = None
    diar.soundfile.read.return_value = ([0.0] * 80_000, 16_000)
    service = diar.module.DiarizationService()
    service.pipeline = MagicMock(side_effect=RuntimeError("inference failed"))

    segments, warnings = service.diarize_with_warnings("call.wav")

    assert warnings == ["DIARIZATION_FAILED_FALLBACK"]
    assert segments[0]["source"] == "mock"


def test_the_allowlist_names_the_classes_the_3_1_checkpoints_need(diar, monkeypatch):
    classes = _checkpoint_classes(monkeypatch)

    diar.module._allow_checkpoint_globals()

    diar.add_safe_globals.assert_called_once()
    (allowed,) = diar.add_safe_globals.call_args.args
    assert set(allowed) == set(classes.values())


def test_a_class_that_cannot_be_imported_is_skipped_and_the_rest_still_allowed(diar, monkeypatch, caplog):
    classes = _checkpoint_classes(monkeypatch)
    monkeypatch.setitem(sys.modules, "omegaconf.listconfig", None)  # the first entry: its module is missing
    monkeypatch.setitem(sys.modules, "torch.torch_version", _module("torch.torch_version"))  # module without the class

    diar.module._allow_checkpoint_globals()

    (allowed,) = diar.add_safe_globals.call_args.args
    assert set(allowed) == {
        classes["omegaconf.dictconfig.DictConfig"],
        classes["pyannote.audio.core.task.Specifications"],
        classes["pyannote.audio.core.task.Problem"],
        classes["pyannote.audio.core.task.Resolution"],
    }
    skipped = [r.getMessage() for r in caplog.records if "not importable; skipped" in r.getMessage()]
    assert len(skipped) == 2
    assert any("omegaconf.listconfig.ListConfig" in m for m in skipped)
    assert any("torch.torch_version.TorchVersion" in m for m in skipped)


def test_an_older_torch_without_the_allowlist_api_is_left_alone(diar, monkeypatch):
    monkeypatch.setattr(diar.module.torch, "serialization", SimpleNamespace())

    diar.module._allow_checkpoint_globals()  # torch before 2.4 has no weights-only default: nothing to do

    diar.add_safe_globals.assert_not_called()


def test_the_allowlisted_names_are_the_real_classes():
    """Heavy lane only: every name resolves, under the path torch's unpickler matches on."""
    for name in ("torch", "soundfile", "huggingface_hub", "omegaconf", "pyannote.audio"):
        pytest.importorskip(name)
    from app.services import diarization_service

    for module_name, name in diarization_service._CHECKPOINT_GLOBALS:
        cls = getattr(importlib.import_module(module_name), name)
        assert f"{cls.__module__}.{cls.__qualname__}" == f"{module_name}.{name}"


def test_loading_allowlists_the_classes_before_the_checkpoint_loads(diar, monkeypatch):
    _checkpoint_classes(monkeypatch)
    order: list[str] = []
    diar.add_safe_globals.side_effect = lambda classes: order.append("allowlist")
    pipeline_cls = MagicMock(name="Pipeline")
    pipeline = MagicMock(name="pipeline")
    pipeline.to.return_value = pipeline

    def from_pretrained(model_id, use_auth_token):
        order.append(f"load {model_id}")
        return pipeline

    pipeline_cls.from_pretrained.side_effect = from_pretrained
    monkeypatch.setattr(diar.module, "_HAS_PYANNOTE", True)
    monkeypatch.setattr(diar.module, "Pipeline", pipeline_cls)
    monkeypatch.setenv("PYANNOTE_AUTH_TOKEN", "test-token")

    service = diar.module.DiarizationService()

    assert order == ["allowlist", "load pyannote/speaker-diarization-3.1"]
    assert service.pipeline is pipeline
