from __future__ import annotations

from lmformatenforcer import JsonSchemaParser

from harness_x.training.lmfe_compat import build_lmfe_prefix_allowed_tokens_fn


class _TinyTokenizer:
    eos_token_id = 0
    all_special_ids = [0]
    _tokens = {
        0: "",
        1: "0",
        2: "{",
        3: "}",
        4: '"',
        5: ":",
        6: "a",
        7: "1",
        8: ",",
        9: " ",
    }

    def __len__(self) -> int:
        return len(self._tokens)

    def encode(self, text: str) -> list[int]:
        assert text == "0"
        return [1]

    def decode(
        self,
        token_ids: list[int],
        clean_up_tokenization_spaces: bool = True,
    ) -> str:
        del clean_up_tokenization_spaces
        return "".join(self._tokens[token_id] for token_id in token_ids)


class _Sent:
    def tolist(self) -> list[int]:
        # The first LMFE callback receives the whole prompt. TokenEnforcer treats that
        # sequence as the root prefix and does not decode it as generated JSON.
        return [999]


def test_lmfe_prefix_adapter_uses_lower_level_api_without_transformers_shim() -> None:
    tokenizer = _TinyTokenizer()
    parser = JsonSchemaParser(
        {
            "type": "object",
            "properties": {"a": {"type": "integer"}},
            "required": ["a"],
            "additionalProperties": False,
        }
    )

    callback, tokenizer_data = build_lmfe_prefix_allowed_tokens_fn(tokenizer, parser)
    allowed = callback(0, _Sent())

    assert tokenizer_data.vocab_size == len(tokenizer)
    assert 2 in allowed  # opening top-level object brace
