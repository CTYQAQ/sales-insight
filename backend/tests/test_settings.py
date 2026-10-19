"""设置模块：脱敏、白名单校验、运行时覆盖与 .env 持久化。

不碰网络（test_llm / test_notify 属于集成面，靠手工与 CI 冒烟覆盖）。
"""
import pytest

from app.core.config import settings
from app.core.errors import DependencyError
from app.modules.settings import service
from app.modules.settings.service import SettingsPatch


@pytest.fixture
def restore_settings():
    """apply_patch 会直接改进程内的 settings 单例，测完必须还原，否则污染其他用例。"""
    originals = {k: getattr(settings, k) for k in SettingsPatch.model_fields}
    yield
    for k, v in originals.items():
        setattr(settings, k, v)


def test_mask_long_secret_shows_only_ends():
    assert service._mask("sk-abcdef1234567890") == "sk-…7890"


def test_mask_short_secret_fully_hidden():
    assert service._mask("abc") == "••••"
    assert service._mask("") == ""


def test_patch_rejects_illegal_enum(restore_settings):
    with pytest.raises(DependencyError, match="LLM_PROVIDER 取值非法"):
        service.apply_patch(SettingsPatch(LLM_PROVIDER="gpt"))


def test_patch_mutates_singleton_and_flags_rebuild(restore_settings, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_env_path", lambda: tmp_path / ".env")
    r = service.apply_patch(SettingsPatch(RAG_HYBRID=False, EMBEDDING_MODEL="bge-m3"))
    assert settings.RAG_HYBRID is False
    assert settings.EMBEDDING_MODEL == "bge-m3"
    assert r["needs_rebuild"] is True
    assert set(r["changed"]) == {"RAG_HYBRID", "EMBEDDING_MODEL"}


def test_patch_secret_echoes_masked_not_plaintext(restore_settings, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_env_path", lambda: tmp_path / ".env")
    r = service.apply_patch(SettingsPatch(DEEPSEEK_API_KEY="sk-test1234567890"))
    assert r["masked"]["DEEPSEEK_API_KEY"] == "sk-…7890"
    assert "sk-test1234567890" not in str(r)


def test_persist_env_replaces_in_place_and_keeps_comments(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# 注释不能丢\nRAG_HYBRID=True\nUNRELATED_KEY=keep-me\n", encoding="utf-8")
    monkeypatch.setattr(service, "_env_path", lambda: env)
    service._persist_env({"RAG_HYBRID": "False", "NEW_KEY": "added"})
    text = env.read_text(encoding="utf-8")
    assert "# 注释不能丢" in text
    assert "RAG_HYBRID=False" in text
    assert "UNRELATED_KEY=keep-me" in text
    assert "NEW_KEY=added" in text
    # 同一个键只出现一次（原位替换，不是追加出重复行）
    assert text.count("RAG_HYBRID=") == 1


def test_snapshot_masks_secrets():
    snap = service.snapshot()
    deepseek = snap["llm"]["providers"]["deepseek"]
    assert deepseek["api_key_set"] is True
    assert "…" in deepseek["api_key_masked"]
    assert snap["system"]["milvus_mode"] in ("lite", "standalone")


def test_snapshot_lists_all_llm_vendors():
    snap = service.snapshot()
    providers = snap["llm"]["providers"]
    assert set(providers) == {"deepseek", "ollama", "openai", "qwen", "zhipu", "moonshot"}
    assert providers["ollama"]["needs_key"] is False
    assert providers["qwen"]["needs_key"] is True


def test_patch_new_vendor_key_masked(restore_settings, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_env_path", lambda: tmp_path / ".env")
    r = service.apply_patch(SettingsPatch(QWEN_API_KEY="sk-dashscope-123456"))
    assert r["masked"]["QWEN_API_KEY"] == "sk-…3456"
    assert settings.QWEN_API_KEY == "sk-dashscope-123456"
