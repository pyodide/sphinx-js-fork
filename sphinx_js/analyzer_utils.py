"""Conveniences shared among analyzers"""

import os
from collections.abc import Callable
from functools import wraps
from json import dump, load
from typing import Any, ParamSpec, TypeVar


def program_name_on_this_platform(program: str) -> str:
    """Return the name of the executable file on the current platform, given a
    command name with no extension."""
    return program + ".cmd" if os.name == "nt" else program


class Command:
    def __init__(self, program: str):
        self.program = program_name_on_this_platform(program)
        self.args: list[str] = []

    def add(self, *args: str) -> None:
        self.args.extend(args)

    def make(self) -> list[str]:
        return [self.program] + self.args


T = TypeVar("T")
P = ParamSpec("P")


def cache_to_file(
    get_filename: Callable[..., str | None]
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Return a decorator that will cache the result of ``get_filename()`` to a
    file

    :arg get_filename: A function which receives the original arguments of the
        decorated function
    """

    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        @wraps(fn)
        def decorated(*args: P.args, **kwargs: P.kwargs) -> Any:
            filename = get_filename(*args, **kwargs)
            if filename and os.path.isfile(filename):
                with open(filename, encoding="utf-8") as f:
                    return load(f)
            res = fn(*args, **kwargs)
            if filename:
                with open(filename, "w", encoding="utf-8") as f:
                    dump(res, f, indent=2)
            return res

        return decorated

    return decorator


def is_explicitly_rooted(path: str) -> bool:
    """Return whether a relative path is explicitly rooted relative to the
    cwd, rather than starting off immediately with a file or folder name.

    It's nice to have paths start with "./" (or "../", "../../", etc.) so, if a
    user is that explicit, we still find the path in the suffix tree.

    """
    return path.startswith(("../", "./")) or path in ("..", ".")


def dotted_path(segments: list[str]) -> str:
    """Convert a JS object path (``['dir/', 'file/', 'class#',
    'instanceMethod']``) to a dotted style that Sphinx will better index.

    Strip off any leading relative-dir segments (./ or ../) because they lead
    to invalid paths like ".....foo". Ultimately, we should thread ``base_dir``
    into this and construct a full path based on that.

    """
    segments_without_separators = [
        s[:-1] for s in segments[:-1] if s not in ["./", "../"]
    ]
    segments_without_separators.append(segments[-1])
    return ".".join(segments_without_separators)
