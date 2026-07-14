"""Enable ``python -m agentcli`` by delegating to the CLI entry point."""

from agentcli.main import main

if __name__ == "__main__":
    main()
