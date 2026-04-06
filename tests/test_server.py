import pytest

from voipbin_mcp.server import validate_page_size


class TestValidatePageSize:
    @pytest.mark.parametrize(
        "input_val, expected",
        [
            (1, 1),
            (10, 10),
            (100, 100),
            (50, 50),
        ],
    )
    def test_in_range_values_pass_through(self, input_val, expected):
        assert validate_page_size(input_val) == expected

    @pytest.mark.parametrize(
        "input_val, expected",
        [
            (0, 1),
            (-1, 1),
            (-100, 1),
        ],
    )
    def test_below_minimum_clamped_to_1(self, input_val, expected):
        assert validate_page_size(input_val) == expected

    @pytest.mark.parametrize(
        "input_val, expected",
        [
            (101, 100),
            (999, 100),
            (1000000, 100),
        ],
    )
    def test_above_maximum_clamped_to_100(self, input_val, expected):
        assert validate_page_size(input_val) == expected

    def test_float_coerced_to_int(self):
        assert validate_page_size(1.5) == 1
        assert validate_page_size(50.9) == 50

    def test_none_returns_default(self):
        assert validate_page_size(None) == 10

    def test_string_returns_default(self):
        assert validate_page_size("abc") == 10

    def test_numeric_string_coerced(self):
        assert validate_page_size("25") == 25
