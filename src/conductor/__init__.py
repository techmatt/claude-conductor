__all__ = ["main"]


def main():
    """The `conductor` command. Imported here, not at the top: importing any `conductor.x`
    runs this file, and a hook's process needs only its own module, not the whole CLI."""
    from .cli import main
    return main()
