"""Compatibility wrapper for the user-facing CLI in :mod:`app.cli`."""

from app.cli import main, sample_player

__all__ = ["main", "sample_player"]


if __name__ == "__main__":
    main()
