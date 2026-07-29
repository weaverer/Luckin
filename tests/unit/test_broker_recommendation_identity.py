import pytest

from lucking.services.broker_recommendation import (
    BrokerRecommendationValidationError,
    normalize_broker_name,
)


def test_unicode_whitespace_is_folded_without_other_normalization() -> None:
    assert normalize_broker_name("\t中信\u3000 证券\n") == "中信 证券"
    assert normalize_broker_name("Ａ证券") != normalize_broker_name("A证券")
    assert normalize_broker_name("ABC") != normalize_broker_name("abc")


def test_empty_broker_name_is_rejected() -> None:
    with pytest.raises(BrokerRecommendationValidationError):
        normalize_broker_name("\u3000\t")
