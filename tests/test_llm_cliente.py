"""Tests de la selección de proveedor LLM (`construir_cliente_llm`).

No llaman a ninguna red: solo verifican que, dado `PROVEEDOR_LLM` y la configuración en
`src.config`, se instancie la clase correcta o se falle con un mensaje claro. Construir
`ClienteGroq`/`ClienteAnthropic`/`ClienteOllama` solo arma el cliente HTTP subyacente, no
dispara ninguna llamada — Ollama no necesita estar corriendo para que estos tests pasen.
"""

from __future__ import annotations

import pytest

from src.llm_cliente import ClienteAnthropic, ClienteGroq, ClienteOllama, construir_cliente_llm


def test_construye_cliente_ollama_por_defecto(monkeypatch):
    monkeypatch.setattr("src.config.PROVEEDOR_LLM", "ollama")
    monkeypatch.setattr("src.config.OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr("src.config.MODELO_LLM", "llama3.1:8b")

    cliente = construir_cliente_llm()

    assert isinstance(cliente, ClienteOllama)


def test_construye_cliente_groq_si_se_configura(monkeypatch):
    monkeypatch.setattr("src.config.PROVEEDOR_LLM", "groq")
    monkeypatch.setattr("src.config.GROQ_API_KEY", "fake-groq-key")
    monkeypatch.setattr("src.config.MODELO_LLM", "openai/gpt-oss-120b")

    cliente = construir_cliente_llm()

    assert isinstance(cliente, ClienteGroq)


def test_construye_cliente_anthropic_si_se_configura(monkeypatch):
    monkeypatch.setattr("src.config.PROVEEDOR_LLM", "anthropic")
    monkeypatch.setattr("src.config.ANTHROPIC_API_KEY", "fake-anthropic-key")
    monkeypatch.setattr("src.config.MODELO_LLM", "claude-sonnet-5")

    cliente = construir_cliente_llm()

    assert isinstance(cliente, ClienteAnthropic)


def test_falla_con_mensaje_claro_si_falta_la_key_del_proveedor_elegido(monkeypatch):
    monkeypatch.setattr("src.config.PROVEEDOR_LLM", "groq")
    monkeypatch.setattr("src.config.GROQ_API_KEY", None)

    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        construir_cliente_llm()


def test_ollama_no_necesita_ninguna_key(monkeypatch):
    """A diferencia de Groq/Anthropic, Ollama es un servidor local: no debe fallar por
    falta de API key aunque ANTHROPIC_API_KEY/GROQ_API_KEY esten vacias."""
    monkeypatch.setattr("src.config.PROVEEDOR_LLM", "ollama")
    monkeypatch.setattr("src.config.GROQ_API_KEY", None)
    monkeypatch.setattr("src.config.ANTHROPIC_API_KEY", None)
    monkeypatch.setattr("src.config.OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr("src.config.MODELO_LLM", "llama3.1:8b")

    cliente = construir_cliente_llm()

    assert isinstance(cliente, ClienteOllama)


def test_falla_con_proveedor_desconocido(monkeypatch):
    monkeypatch.setattr("src.config.PROVEEDOR_LLM", "openai")

    with pytest.raises(RuntimeError, match="PROVEEDOR_LLM"):
        construir_cliente_llm()
