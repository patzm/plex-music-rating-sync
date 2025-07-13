from dataclasses import dataclass
from typing import Callable


class UserPrompt:
    @dataclass
    class MenuOption:
        key: str
        label: str
        help: str = ""
        disabled: bool = False

    HELP_INPUTS = {"?", "h", "help"}
    YES_INPUTS = {"y", "yes"}
    NO_INPUTS = {"n", "no"}

    def _build_prompt(self, message: str, help_text: str | None = None, suffix: str = "") -> str:
        prompt = message
        if help_text:
            prompt += " (type '?' for help)"
        if suffix:
            prompt += f" {suffix}"
        prompt += ": "
        return prompt

    def _get_input(self, prompt: str, lower: bool = True) -> str:
        user_input = input(prompt).strip()
        return user_input.lower() if lower else user_input

    def _print_help(self, help_text: str | None, default: str) -> None:
        print("\n" + (help_text if help_text else default))

    def _print_invalid_input(self, message: str) -> None:
        print(message)

    def choice(
        self, message: str, options: list["UserPrompt.MenuOption"], *, default: int | list[int] | None = None, allow_multiple: bool = False, help_text: str | None = None
    ) -> str | list[str]:
        while True:
            print("\n" + message)
            for idx, opt in enumerate(options, 1):
                print(f"  {idx}) {opt.label}")
            prompt = self._build_prompt(f"Select {'one or more' if allow_multiple else 'one'} [1-{len(options)}]", help_text=help_text)
            user_input = self._get_input(prompt)
            if user_input in self.HELP_INPUTS:
                self._print_help(help_text, "(No help available.)")
                continue
            try:
                if allow_multiple:
                    idxs = [int(i.strip()) for i in user_input.split(",")]
                    if all(1 <= i <= len(options) for i in idxs):
                        return [options[i - 1].key for i in idxs]
                else:
                    idx = int(user_input)
                    if 1 <= idx <= len(options):
                        return options[idx - 1].key
            except ValueError:
                pass
            self._print_invalid_input("Invalid input. Enter a number" + (" or comma-separated list" if allow_multiple else "") + ", or '?' for help.")

    def yes_no(self, message: str, *, default: bool = False) -> bool:
        default_str = "[Y/n]" if default else "[y/N]"
        while True:
            prompt = self._build_prompt(message, help_text=None, suffix=default_str + " (type '?' for help)")
            choice = self._get_input(prompt)
            if choice in self.HELP_INPUTS:
                self._print_help(None, "Please enter 'y' for yes or 'n' for no.")
                continue
            if choice == "" and default is not None:
                return default
            elif choice in self.YES_INPUTS:
                return True
            elif choice in self.NO_INPUTS:
                return False
            else:
                self._print_invalid_input("Please respond with 'y' or 'n'.")

    def text(self, message: str, *, validator: Callable | None = None, help_text: str | None = None) -> str:
        prompt = self._build_prompt(message, help_text=help_text)
        while True:
            user_input = self._get_input(prompt, lower=False)
            if user_input in self.HELP_INPUTS:
                self._print_help(help_text, "No help available.")
                continue
            if validator is None or validator(user_input):
                return user_input
            self._print_invalid_input("Invalid input. Try again or type '?' for help.")

    def confirm_continue(self, message: str = "Press Enter to continue or 'q' to quit: ") -> bool:
        response = self._get_input(message)
        return response != "q"
