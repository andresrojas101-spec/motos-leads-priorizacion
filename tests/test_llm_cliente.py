"""Tests de la selección de proveedor LLM (`construir_cliente_llm`).

No llaman a ninguna red: solo verifican que, dado `PROVEEDOR_LLM` y las API keys en
`src.config`, se instancie la clase correcta o se falle con un mensaje claro. La
construcción de `ClienteGroq`/`ClienteAnthropic` solo arma el cliente HTTP subyacente,
no dispara ninguna llamada.
"""

from __future__ import annotations

import pytest

from src.llm_cliente import ClienteAnthropic, ClienteGroq, construir_cliente_llm


def test_construye_cliente_groq_por_defecto(monkeypatch):
    monkeypatch.setattr("src.config.PROVEEDOR_LLM", "groq")
    monkeypatch.setattr("src.config.GROQ_API_KEY", "fake-groq-key")
    monkeypatch.setattr("src.config.MODELO_LLM", "llama-3.3-70b-versatile")

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


def test_falla_con_proveedor_desconocido(monkeypatch):
    monkeypatch.setattr("src.config.PROVEEDOR_LLM", "openai")

    with pytest.raises(RuntimeError, match="PROVEEDOR_LLM"):
        construir_cliente_llm()
