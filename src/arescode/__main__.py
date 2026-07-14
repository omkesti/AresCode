"""Enable ``python -m arescode`` by delegating to the CLI entry point."""

from arescode.main import main

if __name__ == "__main__":
    main()
