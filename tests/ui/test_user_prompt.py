import pytest

from ui.prompt import UserPrompt


class TestUserPromptChoiceHelp:
    """Test UserPrompt.choice help input for all help options and case insensitivity."""

    @pytest.mark.parametrize("help_input", ["?", "h", "H", "help", "HELP"])
    def test_choice_all_help_inputs_show_help(self, monkeypatch, capsys, help_input):
        prompt_inputs = iter([help_input, "1"])
        monkeypatch.setattr("builtins.input", lambda _: next(prompt_inputs))
        options = [UserPrompt.MenuOption(key="A", label="Option A"), UserPrompt.MenuOption(key="B", label="Option B")]
        result = UserPrompt().choice("Choose one", options, help_text="Help for choice")
        captured = capsys.readouterr()
        assert "Help for choice" in captured.out
        assert result == "A"

    @pytest.mark.parametrize(
        "inputs,expected_result,expect_help",
        [
            (["?", "1"], "A", True),
            (["1"], "A", False),
            (["2"], "B", False),
            (["3", "1"], "A", False),  # invalid, then valid
        ],
    )
    def test_choice_valid_and_invalid_inputs(self, monkeypatch, capsys, inputs, expected_result, expect_help):
        prompt_inputs = iter(inputs)
        monkeypatch.setattr("builtins.input", lambda _: next(prompt_inputs))
        options = [UserPrompt.MenuOption(key="A", label="Option A"), UserPrompt.MenuOption(key="B", label="Option B")]
        result = UserPrompt().choice("Choose one", options, help_text="Help for choice")
        captured = capsys.readouterr()
        if expect_help:
            assert "Help for choice" in captured.out
        else:
            assert "Help for choice" not in captured.out
        assert result == expected_result


class TestUserPromptChoiceMultiple:
    """Covers allow_multiple=True branch in UserPrompt.choice, including ValueError and help scenarios."""

    @pytest.mark.parametrize(
        "inputs,expected_result,expect_help,expect_invalid",
        [
            (["1,2"], ["A", "B"], False, False),
            (["2,1"], ["B", "A"], False, False),
            (["1,3", "1,2"], ["A", "B"], False, True),  # invalid, then valid
            (["a,b", "1,2"], ["A", "B"], False, True),  # non-integer, then valid (captures ValueError)
            (["?", "1,2"], ["A", "B"], True, False),  # help then valid
        ],
    )
    def test_choice_multiple_selection(self, monkeypatch, capsys, inputs, expected_result, expect_help, expect_invalid):
        prompt_inputs = iter(inputs)
        monkeypatch.setattr("builtins.input", lambda _: next(prompt_inputs))
        options = [UserPrompt.MenuOption(key="A", label="Option A"), UserPrompt.MenuOption(key="B", label="Option B")]
        result = UserPrompt().choice("Choose one or more", options, allow_multiple=True, help_text="Help for multiple")
        captured = capsys.readouterr()
        if expect_help:
            assert "Help for multiple" in captured.out
        else:
            assert "Help for multiple" not in captured.out
        if expect_invalid:
            assert "Invalid input. Enter a number or comma-separated list, or '?' for help." in captured.out
        else:
            assert "Invalid input. Enter a number or comma-separated list, or '?' for help." not in captured.out
        assert result == expected_result


class TestUserPromptYesNoHelp:
    """Test UserPrompt.yes_no help input for all help options and case insensitivity."""

    @pytest.mark.parametrize(
        "inputs,default,expected_result,expect_help",
        [
            (["y"], True, True, False),
            (["n"], False, False, False),
            (["?", "N"], False, False, True),
            (["", "y"], True, True, False),
            (["maybe", "y"], True, True, False),
        ],
    )
    def test_yes_no_valid_and_invalid_inputs(self, monkeypatch, capsys, inputs, default, expected_result, expect_help):
        prompt_inputs = iter(inputs)
        monkeypatch.setattr("builtins.input", lambda _: next(prompt_inputs))
        result = UserPrompt().yes_no("Proceed?", default=default)
        captured = capsys.readouterr()
        if expect_help:
            assert "Please enter 'y' for yes or 'n' for no." in captured.out
        else:
            assert "Please enter 'y' for yes or 'n' for no." not in captured.out
        assert result is expected_result


class TestUserPromptTextHelp:
    """Test UserPrompt.text help input for all help options and case insensitivity."""

    @pytest.mark.parametrize(
        "inputs,expected_result,expect_help",
        [
            (["?", "good"], "good", True),
            (["bad", "good"], "good", False),  # invalid, then valid
        ],
    )
    def test_text_valid_and_invalid_inputs(self, monkeypatch, capsys, inputs, expected_result, expect_help):
        def validator(x):
            return x == "good"

        prompt_inputs = iter(inputs)
        monkeypatch.setattr("builtins.input", lambda _: next(prompt_inputs))
        result = UserPrompt().text("Enter value", validator=validator, help_text="Help for text")
        captured = capsys.readouterr()
        if expect_help:
            assert "Help for text" in captured.out
        else:
            assert "Help for text" not in captured.out
        assert result == expected_result


class TestUserPromptConfirmContinue:
    """Tests for UserPrompt.confirm_continue method covering continue, quit, and retry scenarios."""

    @pytest.mark.parametrize(
        "user_input,expected",
        [
            ("", True),
            ("q", False),
            ("Q", False),
            ("literally anything", True),
            ("?", True),
        ],
    )
    def test_confirm_continue_various_inputs_returns_expected(self, monkeypatch, user_input, expected):
        monkeypatch.setattr("builtins.input", lambda _: user_input)
        result = UserPrompt().confirm_continue()
        assert result == expected
