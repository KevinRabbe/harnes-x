"""Small LM Format Enforcer adapter independent of its Transformers shim.

LM Format Enforcer 0.11.3's optional ``integrations.transformers`` module imports
``PreTrainedTokenizerBase`` from locations that changed in Transformers 5.x.  Harness X
only needs the stable lower-level TokenEnforcer API, so we build the same tokenizer
metadata and prefix callback directly instead of monkey-patching Transformers.
"""

from __future__ import annotations

from typing import Any, Callable


def build_lmfe_tokenizer_data(tokenizer: Any) -> Any:
    """Build LMFE tokenizer metadata using the same semantics as its HF integration."""

    try:
        from lmformatenforcer import TokenEnforcerTokenizerData
    except ImportError as exc:  # pragma: no cover - optional operator dependency
        raise RuntimeError(
            "schema-constrained repair requires lm-format-enforcer; install "
            "lm-format-enforcer==0.11.3"
        ) from exc

    vocab_size = len(tokenizer)
    token_zero = tokenizer.encode("0")[-1]
    special_ids = set(getattr(tokenizer, "all_special_ids", ()))
    regular_tokens: list[tuple[int, str, bool]] = []

    for token_id in range(vocab_size):
        if token_id in special_ids:
            continue
        decoded_after_zero = tokenizer.decode([token_zero, token_id])[1:]
        decoded_regular = tokenizer.decode([token_id])
        is_word_start = len(decoded_after_zero) > len(decoded_regular)
        regular_tokens.append((token_id, decoded_after_zero, is_word_start))

    def decode(tokens: list[int]) -> str:
        return tokenizer.decode(
            tokens,
            clean_up_tokenization_spaces=False,
        ).rstrip("�")

    return TokenEnforcerTokenizerData(
        regular_tokens,
        decode,
        tokenizer.eos_token_id,
        False,
        vocab_size,
    )


def build_lmfe_prefix_allowed_tokens_fn(
    tokenizer: Any,
    parser: Any,
    *,
    tokenizer_data: Any | None = None,
) -> tuple[Callable[[int, Any], list[int]], Any]:
    """Return a Transformers-compatible prefix callback plus reusable tokenizer data."""

    try:
        from lmformatenforcer import TokenEnforcer
    except ImportError as exc:  # pragma: no cover - optional operator dependency
        raise RuntimeError(
            "schema-constrained repair requires lm-format-enforcer; install "
            "lm-format-enforcer==0.11.3"
        ) from exc

    data = tokenizer_data or build_lmfe_tokenizer_data(tokenizer)
    enforcer = TokenEnforcer(data, parser)

    def allowed_tokens(_batch_id: int, sent: Any) -> list[int]:
        return enforcer.get_allowed_tokens(sent.tolist()).allowed_tokens

    return allowed_tokens, data
