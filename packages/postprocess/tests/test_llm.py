from unittest.mock import patch, MagicMock
from resona_postprocess.llm import llm_postprocess


@patch("resona_postprocess.llm.litellm")
@patch("resona_postprocess.llm.config")
def test_calls_litellm_completion(mock_config, mock_litellm):
    mock_config.side_effect = lambda key, default="": {
        "RESONA_LLM_MODEL": "gpt-4o-mini",
        "RESONA_LLM_API_BASE": "",
    }.get(key, default)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "formatted text"
    mock_litellm.completion.return_value = mock_response

    result = llm_postprocess("raw text", prompt="Format this.")
    assert result == "formatted text"
    mock_litellm.completion.assert_called_once()

    call_kwargs = mock_litellm.completion.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "Format this."
    assert messages[1]["content"] == "raw text"


@patch("resona_postprocess.llm.litellm")
@patch("resona_postprocess.llm.config")
def test_explicit_model_overrides_env(mock_config, mock_litellm):
    mock_config.side_effect = lambda key, default="": default

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "ok"
    mock_litellm.completion.return_value = mock_response

    llm_postprocess("text", prompt="p", model="ollama/llama3")

    call_kwargs = mock_litellm.completion.call_args
    assert call_kwargs.kwargs.get("model") == "ollama/llama3"


@patch("resona_postprocess.llm.litellm")
@patch("resona_postprocess.llm.config")
def test_api_base_passed_when_set(mock_config, mock_litellm):
    mock_config.side_effect = lambda key, default="": {
        "RESONA_LLM_MODEL": "gpt-4o-mini",
        "RESONA_LLM_API_BASE": "http://localhost:11434",
    }.get(key, default)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "ok"
    mock_litellm.completion.return_value = mock_response

    llm_postprocess("text", prompt="p")

    call_kwargs = mock_litellm.completion.call_args
    assert call_kwargs.kwargs.get("api_base") == "http://localhost:11434"
